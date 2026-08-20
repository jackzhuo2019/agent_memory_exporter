# Architecture

## Overview

Agent-Memory-Exporter is a two-layer system:

| Layer | Package | Responsibility | Dependencies |
|-------|---------|---------------|--------------|
| **CLI** | `packages/cli` | Read source data → reassemble turns → redact PII → write `raw/*.json` | sqlite3, click, pydantic, pyyaml |
| **MCP Server** | `packages/mcp_server` | Read `raw/*.json` → LLM cleaning → `processed/*.md` → WeKnora API | mcp, httpx, pydantic |
| **Shared** | `packages/shared` | Common Pydantic models, JSON Schema, path constants | pydantic, jsonschema |

The CLI and MCP Server communicate **only** through the `agent_export/raw/*.json` file contract defined by `schemas/raw_session.schema.json`. The CLI never calls LLMs or HTTP APIs. The MCP Server never reads source databases directly.

## Data Sources

### WorkBuddy

```
~/.workbuddy/
├── workbuddy.db                          # SQLite: sessions table (metadata)
│   └── sessions: id, cwd, title, created_at, updated_at, mode, model, status
├── projects/<cwd-hash>/
│   ├── <conversation-id>.jsonl           # Main conversation (one event per line)
│   └── agent-<hash>.jsonl                # Sub-agent conversations (parentId links to main)
├── traces/<PID>/trace_*.json             # OpenTelemetry spans (tool-call perf, NOT conversation text)
├── workspace/sessions/<uuid>/            # Filesystem snapshots (NOT conversation text)
├── memory/<uuid>_memory.md               # WorkBuddy's own persistent memory
└── sessions/<PID>.json                   # Runtime heartbeat files
```

**Key mapping**:
- `sessions.id` (DB) == `<conversation-id>.jsonl` filename (in `projects/<cwd-hash>/`)
- `sessions.cwd` → `projects/<cwd-hash>/` directory name via simple escaping (lowercase + `\`→`-`), with traversal fallback
- `agent-*.jsonl` files have their own `sessionId` and `parentId` linking to a message in the main conversation

**jsonl event types** (one JSON object per line):

| `type` | `role` | Description |
|--------|--------|-------------|
| `message` | `user` | User input — **starts a new turn** |
| `message` | `assistant` | Assistant response |
| `function_call` | — | Tool invocation (name, arguments) |
| `function_call_result` | — | Tool return value (links to preceding function_call) |
| `reasoning` | — | Model's chain-of-thought |
| `file-history-snapshot` | — | Filesystem state snapshot (optional to keep) |
| `ai-title` | — | AI-generated session title (metadata, not conversation) |

### OpenCode

```
~/.local/share/opencode/opencode.db      # SQLite (913MB)
├── session: id, project_id, directory, title, model, agent, time_created, time_updated
├── session_message: id, session_id, type, seq, data (JSON)
├── part: id, message_id, session_id, data (JSON: role, content, tool calls)
├── session_input: id, session_id, prompt, delivery, time_created
├── project: id, worktree, name
└── message: id, session_id, data (legacy/alternative path)
```

OpenCode adapter reads `session_message` + `part` tables, ordered by `seq`, parsing `part.data` JSON for role/content.

## Turn Assembly

A **turn** is defined as: one user message + all subsequent events until the next user message.

```
Turn 0: [user msg] → [reasoning] → [assistant msg] → [function_call] → [function_call_result]
Turn 1: [user msg] → [assistant msg]
Turn 2: [user msg] → [reasoning] → [function_call] → [function_call_result] → [assistant msg]
```

Edge cases:
- **Preamble before first user message**: stored as `turn_index = -1` (pre-conversation context)
- **Consecutive user messages** (no assistant between): each starts a new turn
- **Missing function_call_result**: logged in `gaps.json`, function_call still exported with `tool_output = null`

## Incremental Export

`state.json` tracks:

```json
{
  "schema_version": "1.0.0",
  "last_export_ts": "2026-08-20T12:00:00Z",
  "exported_sessions": {
    "<session_id>": {
      "updated_at": "...",
      "turn_count": 42,
      "exported_at": "...",
      "file": "raw/<session_id>.json",
      "content_hash": "sha256:..."
    }
  },
  "export_count": 142
}
```

- `--incremental` (default): only sessions where `updated_at > last_export_ts`
- `--full`: re-export all sessions, overwrite state.json
- If a session's `content_hash` changed since last export, it is re-exported even if `updated_at` is unchanged

## PII Redaction

CLI-stage redaction uses `config/redaction_rules.yaml`:

```yaml
patterns:
  - name: api_key_openai
    regex: 'sk-[A-Za-z0-9]{20,}'
    replace: '[REDACTED_API_KEY]'
  - name: email
    regex: '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    replace: '[REDACTED_EMAIL]'
  - name: phone_cn
    regex: '1[3-9]\d{9}'
    replace: '[REDACTED_PHONE]'
```

Redaction runs on `user_message`, `content`, `tool_input`, and `tool_output` fields before writing to `raw/*.json`. A `redaction_report.json` records hit counts per rule (no original text retained).

LLM-stage cleaning (MCP Server) handles semantic-level PII (real names, project codenames) that regex cannot catch.

## MCP Server Tools

| Tool | Input | Output | Description |
|------|-------|--------|-------------|
| `export` | `source`, `since?`, `full?` | `{exported, skipped, errors}` | Triggers CLI core (in-process import or subprocess) |
| `clean` | `session_ids?`, `model?` | `{cleaned, trash_filtered}` | Reads `raw/*.json`, LLM filters/cleans, writes `processed/*.md` |
| `ingest` | `kb_id`, `session_ids?`, `dry_run?` | `{ingested, failed, kb_id}` | Uploads `processed/*.md` to WeKnora via HTTP API |

The MCP Server holds the cleaning prompt in `prompts/clean_prompt.md` — hot-updatable without CLI changes.

## WeKnora Integration

WeKnora MCP Server (`weknora_mcp_server`) exposes:
- `create_knowledge_from_file(kb_id, file_path)` — upload md file to Source-KB
- `list_knowledge_bases()` — list available KBs
- `hybrid_search(kb_id, query)` — semantic + keyword search

The `ingest` tool calls the HTTP API directly (`POST /knowledge-bases/{kb_id}/knowledge/file`) rather than going through the MCP server, to avoid double-hop.

Auto-Wiki distillation is a **separate scheduled task** (cron), not part of the MCP Server. It reads Source-KB content, identifies high-frequency topics, and writes distilled wiki entries back to WeKnora — with `provenance` tracking (source session IDs).
