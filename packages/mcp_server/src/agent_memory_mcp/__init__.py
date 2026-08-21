"""Agent Memory MCP Server.

Provides MCP tools for the agent-memory-exporter pipeline:
  - export: trigger CLI export (in-process import)
  - clean: LLM cleaning of raw JSON → processed markdown (Phase 5, F04)
  - ingest: upload processed markdown to WeKnora (Phase 5, F05)
"""

__version__ = "0.1.0"
