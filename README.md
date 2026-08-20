# Agent-Memory-Exporter

Read-only CLI tool that extracts agent conversation data from local sources (WorkBuddy, OpenCode), reassembles complete user↔assistant turn sequences, applies PII redaction, and exports standardized raw JSON for downstream LLM cleaning and knowledge-base ingestion.

## Architecture

```
agent-memory-exporter (monorepo)
├── packages/shared/       — shared types, JSON Schema, path constants
├── packages/cli/          — read-only CLI: source → raw/*.json
├── packages/mcp_server/   — MCP server: raw/*.json → LLM clean → WeKnora ingest
├── schemas/               — raw_session.schema.json (CLI ↔ MCP contract)
├── config/                — redaction_rules.yaml, mcp_config.yaml
└── docs/                  — design docs
```

## Data Flow

```
WorkBuddy DB + projects/*.jsonl
        ↓  CLI (read-only)
1. detect paths
2. adapter reads DB + jsonl
3. assembler reassembles turns (user-message boundary)
4. redactor applies PII rules
5. exporter writes raw/*.json + state.json + gaps.json
        ↓  (shared directory)
MCP Server
6. clean tool: raw/*.json → LLM → processed/*.md
7. ingest tool: processed/*.md → WeKnora HTTP API
        ↓
WeKnora Source-KB (RAG base)
        ↓  (scheduled task, separate from MCP)
Auto-Wiki distillation (high-frequency topics → wiki)
```

## Quick Start

```bash
# Install (editable, from repo root)
uv sync

# Export all WorkBuddy sessions
uv run agent-memory-exporter export --source workbuddy

# Export only sessions updated since last run (incremental)
uv run agent-memory-exporter export --source workbuddy --incremental

# Full re-export (ignore state)
uv run agent-memory-exporter export --source workbuddy --full

# Show export status
uv run agent-memory-exporter status
```

## Key Principles

- **Read-only**: never modifies source databases or trace files.
- **Incremental**: tracks `last_export_ts` in `state.json`; only re-exports updated sessions.
- **PII redaction at CLI stage**: regex-based rules in `config/redaction_rules.yaml` run before any JSON is written to disk.
- **Adapter pattern**: `SourceAdapter` protocol abstracts data sources; adding OpenCode or other agents only requires a new adapter.
- **Schema contract**: `schemas/raw_session.schema.json` defines the JSON shape; CLI validates on write, MCP validates on read.

See [docs/](docs/) for detailed design.
