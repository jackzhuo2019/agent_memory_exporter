"""Tests for upload state tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_mcp.upload_state import (
    MAX_RETRIES,
    UploadRecord,
    UploadState,
    can_retry,
    is_uploaded,
    load_upload_state,
    record_failure,
    record_skip,
    record_success,
    save_upload_state,
)


class TestUploadState:
    def test_save_and_load_empty(self, tmp_path: Path):
        state = UploadState()
        path = tmp_path / "upload_state.json"
        save_upload_state(state, path)
        assert path.exists()

        loaded = load_upload_state(path)
        assert loaded.schema_version == "1.0.0"
        assert loaded.records == {}
        assert loaded.total_uploaded == 0

    def test_load_nonexistent(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        state = load_upload_state(path)
        assert state.records == {}

    def test_record_success(self):
        state = UploadState()
        record_success(state, "sess-1", "sess-1.md", "high", "knowledge-abc")
        assert "sess-1" in state.records
        rec = state.records["sess-1"]
        assert rec.status == "uploaded"
        assert rec.knowledge_id == "knowledge-abc"
        assert rec.quality == "high"
        assert rec.uploaded_at is not None
        assert state.total_uploaded == 1
        assert state.last_upload_ts is not None

    def test_record_failure(self):
        state = UploadState()
        record_failure(state, "sess-1", "sess-1.md", "medium", "timeout")
        rec = state.records["sess-1"]
        assert rec.status == "failed"
        assert rec.retries == 1
        assert rec.error == "timeout"
        assert state.total_failed == 1

    def test_record_failure_increments_retries(self):
        state = UploadState()
        record_failure(state, "sess-1", "sess-1.md", "medium", "err1")
        record_failure(state, "sess-1", "sess-1.md", "medium", "err2")
        record_failure(state, "sess-1", "sess-1.md", "medium", "err3")
        rec = state.records["sess-1"]
        assert rec.retries == 3
        assert rec.error == "err3"

    def test_record_skip(self):
        state = UploadState()
        record_skip(state, "sess-1", "sess-1.md", "low")
        rec = state.records["sess-1"]
        assert rec.status == "skipped"
        assert rec.quality == "low"

    def test_is_uploaded(self):
        state = UploadState()
        assert is_uploaded(state, "sess-1") is False
        record_success(state, "sess-1", "sess-1.md", "high", "k-1")
        assert is_uploaded(state, "sess-1") is True

    def test_can_retry_no_record(self):
        state = UploadState()
        assert can_retry(state, "sess-1") is True

    def test_can_retry_under_max(self):
        state = UploadState()
        record_failure(state, "sess-1", "sess-1.md", "high", "err")
        assert can_retry(state, "sess-1") is True

    def test_can_retry_at_max(self):
        state = UploadState()
        for _ in range(MAX_RETRIES):
            record_failure(state, "sess-1", "sess-1.md", "high", "err")
        assert can_retry(state, "sess-1") is False

    def test_save_and_load_with_records(self, tmp_path: Path):
        state = UploadState()
        record_success(state, "sess-1", "sess-1.md", "high", "k-1")
        record_failure(state, "sess-2", "sess-2.md", "medium", "timeout")
        record_skip(state, "sess-3", "sess-3.md", "low")

        path = tmp_path / "upload_state.json"
        save_upload_state(state, path)

        loaded = load_upload_state(path)
        assert "sess-1" in loaded.records
        assert loaded.records["sess-1"].status == "uploaded"
        assert loaded.records["sess-1"].knowledge_id == "k-1"
        assert "sess-2" in loaded.records
        assert loaded.records["sess-2"].status == "failed"
        assert "sess-3" in loaded.records
        assert loaded.records["sess-3"].status == "skipped"
        assert loaded.total_uploaded == 1
        assert loaded.total_failed == 1
