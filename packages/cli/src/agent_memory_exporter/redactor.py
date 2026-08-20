"""PII redaction: regex-based pattern matching applied before writing raw JSON.

Rules are loaded from config/redaction_rules.yaml. Redaction is applied to:
  - Turn.user_message
  - Event.content
  - Event.tool_input
  - Event.tool_output

A redaction report (hit counts per rule) is produced but never stores original text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent_memory_shared.models import RawSession


@dataclass
class RedactionRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str
    hit_count: int = 0


@dataclass
class RedactionReport:
    total_hits: int = 0
    rule_hits: dict[str, int] = field(default_factory=dict)
    sessions_processed: int = 0

    def to_dict(self) -> dict:
        return {
            "total_hits": self.total_hits,
            "sessions_processed": self.sessions_processed,
            "rule_hits": dict(sorted(self.rule_hits.items(), key=lambda x: -x[1])),
        }


class Redactor:
    """Apply regex-based PII redaction to session content."""

    def __init__(self, rules_path: str | Path | None = None) -> None:
        if rules_path is None:
            # Default: look for config/redaction_rules.yaml relative to project root
            rules_path = Path(__file__).resolve().parents[4] / "config" / "redaction_rules.yaml"
        self.rules_path = Path(rules_path)
        self.rules: list[RedactionRule] = []
        self._load_rules()

    def _load_rules(self) -> None:
        """Load redaction rules from YAML config."""
        if not self.rules_path.exists():
            # No rules file → no redaction
            self.rules = []
            return

        with open(self.rules_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        for rule_def in config.get("patterns", []):
            name = rule_def["name"]
            regex = rule_def["regex"]
            replacement = rule_def.get("replace", f"[REDACTED_{name.upper()}]")
            try:
                pattern = re.compile(regex)
            except re.error as e:
                import sys

                print(f"WARNING: Invalid regex in rule '{name}': {e}", file=sys.stderr)
                continue
            self.rules.append(RedactionRule(name=name, pattern=pattern, replacement=replacement))

    def redact_text(self, text: str) -> str:
        """Apply all redaction rules to a text string."""
        if not text:
            return text
        for rule in self.rules:
            matches = rule.pattern.findall(text)
            if matches:
                rule.hit_count += len(matches)
            text = rule.pattern.sub(rule.replacement, text)
        return text

    def redact_session(self, session: RawSession) -> RedactionReport:
        """Apply redaction to all text fields in a session. Modifies session in-place.

        Returns a RedactionReport with hit counts.
        """
        for turn in session.turns:
            turn.user_message = self.redact_text(turn.user_message)
            for evt in turn.events:
                evt.content = self.redact_text(evt.content)
                if evt.tool_input:
                    evt.tool_input = self.redact_text(evt.tool_input)
                if evt.tool_output:
                    evt.tool_output = self.redact_text(evt.tool_output)

        report = RedactionReport(sessions_processed=1)
        report.total_hits = sum(r.hit_count for r in self.rules)
        report.rule_hits = {r.name: r.hit_count for r in self.rules if r.hit_count > 0}

        # Reset hit counts for next session
        for r in self.rules:
            r.hit_count = 0

        return report

    def redact_sessions(self, sessions: list[RawSession]) -> RedactionReport:
        """Apply redaction to multiple sessions. Returns aggregate report."""
        aggregate = RedactionReport()
        for session in sessions:
            report = self.redact_session(session)
            aggregate.sessions_processed += 1
            aggregate.total_hits += report.total_hits
            for name, count in report.rule_hits.items():
                aggregate.rule_hits[name] = aggregate.rule_hits.get(name, 0) + count
        return aggregate
