"""Tests for the PII redactor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_memory_shared.models import Event, EventRole, RawSession, SessionMeta, Turn
from agent_memory_exporter.redactor import Redactor


class TestRedactor:
    def _make_rules_file(self, tmp_path: Path) -> Path:
        """Create a minimal redaction rules file."""
        rules_path = tmp_path / "redaction_rules.yaml"
        rules_path.write_text(
            """
patterns:
  - name: api_key
    regex: 'sk-[A-Za-z0-9]{20,}'
    replace: '[REDACTED_API_KEY]'
  - name: email
    regex: '[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}'
    replace: '[REDACTED_EMAIL]'
""",
            encoding="utf-8",
        )
        return rules_path

    def _make_session(self) -> RawSession:
        return RawSession(
            session_id="test-001",
            source="workbuddy",
            session_meta=SessionMeta(title="Test"),
            turns=[
                Turn(
                    turn_index=0,
                    user_message="My API key is sk-abc123def456ghi789jkl012mno345 and email is test@example.com",
                    user_timestamp=datetime.now(timezone.utc),
                    events=[
                        Event(
                            role=EventRole.ASSISTANT,
                            content="I see your key sk-abc123def456ghi789jkl012mno345",
                            timestamp=datetime.now(timezone.utc),
                        ),
                    ],
                ),
            ],
        )

    def test_redact_text(self, tmp_path):
        rules_path = self._make_rules_file(tmp_path)
        redactor = Redactor(rules_path=rules_path)
        text = "Contact me at test@example.com with key sk-abc123def456ghi789jkl012mno345"
        result = redactor.redact_text(text)
        assert "test@example.com" not in result
        assert "sk-abc123def456ghi789jkl012mno345" not in result
        assert "[REDACTED_EMAIL]" in result
        assert "[REDACTED_API_KEY]" in result

    def test_redact_session(self, tmp_path):
        rules_path = self._make_rules_file(tmp_path)
        redactor = Redactor(rules_path=rules_path)
        session = self._make_session()

        report = redactor.redact_session(session)

        assert report.total_hits > 0
        assert "api_key" in report.rule_hits
        assert "email" in report.rule_hits
        assert "sk-abc123def456ghi789jkl012mno345" not in session.turns[0].user_message
        assert "[REDACTED_API_KEY]" in session.turns[0].user_message
        assert "[REDACTED_EMAIL]" in session.turns[0].user_message

    def test_no_rules_file(self, tmp_path):
        """If no rules file exists, redaction is a no-op."""
        redactor = Redactor(rules_path=tmp_path / "nonexistent.yaml")
        session = self._make_session()
        original_msg = session.turns[0].user_message
        report = redactor.redact_session(session)
        assert report.total_hits == 0
        assert session.turns[0].user_message == original_msg

    def test_empty_text(self, tmp_path):
        rules_path = self._make_rules_file(tmp_path)
        redactor = Redactor(rules_path=rules_path)
        assert redactor.redact_text("") == ""
        assert redactor.redact_text(None) is None
