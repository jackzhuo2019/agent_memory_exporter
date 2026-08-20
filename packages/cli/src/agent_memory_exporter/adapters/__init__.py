"""Source adapter abstractions.

Each adapter (WorkBuddy, OpenCode) implements the SourceAdapter protocol
to read sessions from its respective data source.
"""

from agent_memory_exporter.adapters.base import SourceAdapter, SessionRef

__all__ = ["SourceAdapter", "SessionRef"]
