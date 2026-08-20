# Implementation Plan

## Phases

### Phase 1: Skeleton + Shared + Schemas ✅ (current)

**Goal**: Project structure, JSON Schema contract, shared Pydantic models.

**Files**:
- `pyproject.toml` — uv workspace root
- `packages/shared/` — Pydantic models (`RawSession`, `Turn`, `Event`, `SessionRef`, `ExportState`), schema loader, path constants
- `schemas/raw_session.schema.json` — JSON Schema for raw session output

**Verification**: `python -c "from agent_memory_shared.models import RawSession"` works.

---

### Phase 2: CLI Core — WorkBuddy Adapter + Assembler + Exporter

**Goal**: `agent-memory-exporter export --source workbuddy` produces real `raw/*.json` from live data.

**Files**:
- `packages/cli/src/agent_memory_exporter/adapters/base.py` — `SourceAdapter` Protocol, `SessionRef`
- `packages/cli/src/agent_memory_exporter/adapters/workbuddy.py` — reads `workbuddy.db` sessions table + `projects/<cwd-hash>/*.jsonl`
- `packages/cli/src/agent_memory_exporter/adapters/opencode.py` — stub (raises NotImplementedError for now)
- `packages/cli/src/agent_memory_exporter/assembler.py` — turn assembly (user-message boundary)
- `packages/cli/src/agent_memory_exporter/exporter.py` — writes `raw/*.json` + `manifest.json`
- `packages/cli/src/agent_memory_exporter/cli.py` — click CLI: `export`, `status` commands
- `packages/cli/src/agent_memory_exporter/__main__.py` — `python -m agent_memory_exporter`

**WorkBuddy adapter logic**:
1. Open `~/.workbuddy/workbuddy.db`, query `sessions` table (filtered by `updated_at > since` if incremental)
2. For each session: escape `cwd` → find `projects/<cwd-hash>/` directory (fallback: traverse and match `cwd` field in jsonl)
3. Read `<conversation-id>.jsonl` line by line, parse each event
4. Also read `agent-*.jsonl` files, attach to parent session via `parentId`
5. Return `list[Event]` to assembler

**Assembler logic**:
1. Sort events by `timestamp`
2. Iterate: each `message` with `role=user` starts a new `Turn`
3. All other events append to the current turn's `events` list
4. Events before the first user message → `Turn(turn_index=-1, user_message="", events=[...])` (preamble)
5. Match `function_call` → `function_call_result` by `parentId` / `callId`

**Verification**:
```bash
uv run agent-memory-exporter export --source workbuddy --limit 3
# Check agent_export/raw/*.json has correct turn structure
```

---

### Phase 3: Redaction + State + Gap Detection

**Goal**: PII redaction, incremental export, data integrity reporting.

**Files**:
- `packages/cli/src/agent_memory_exporter/redactor.py` — regex-based PII redaction
- `packages/cli/src/agent_memory_exporter/state.py` — `state.json` read/write, incremental logic
- `packages/cli/src/agent_memory_exporter/gap_detector.py` — detect missing jsonl, broken events, orphan function_call_results
- `config/redaction_rules.yaml` — default rules (API keys, emails, phone numbers, file paths)

**Redactor logic**:
- Load `config/redaction_rules.yaml`
- For each `Turn.user_message` and `Event.content/tool_input/tool_output`: apply all regex patterns, replace with `[REDACTED_<NAME>]`
- Record hit counts in `redaction_report.json`
- Never store original matched text

**State logic**:
- `state.json` stores `last_export_ts` and per-session metadata
- `--incremental` (default): `list_sessions(since=last_export_ts)`
- `--full`: re-export everything, reset state
- Content hash (`sha256` of canonical JSON) detects changes even when `updated_at` is unchanged

**Gap detector logic**:
- Check: jsonl file exists for every session in DB?
- Check: every `function_call` has a matching `function_call_result`?
- Check: timestamps are monotonically non-decreasing within a session?
- Output: `gaps.json` with `{session_id, gap_type, details}` entries

**Verification**:
```bash
uv run agent-memory-exporter export --source workbuddy --incremental
# Check agent_export/state.json, redaction_report.json, gaps.json
uv run agent-memory-exporter export --source workbuddy --incremental  # second run should skip all
uv run agent-memory-exporter export --source workbuddy --full          # force re-export
```

---

### Phase 4: OpenCode Adapter (deferred)

**Goal**: `--source opencode` works the same as `--source workbuddy`.

**Files**:
- `packages/cli/src/agent_memory_exporter/adapters/opencode.py` — full implementation

**Logic**:
1. Open `~/.local/share/opencode/opencode.db`
2. Query `session` table (filtered by `time_updated > since`)
3. For each session: query `session_message` + `part` tables, ordered by `seq`
4. Parse `part.data` JSON → extract role/content/tool info
5. Return `list[Event]` to assembler

**Note**: OpenCode DB is 913MB — must use pagination (`LIMIT/OFFSET` or `WHERE id > ?` cursor) to avoid loading everything into memory.

---

### Phase 5: MCP Server (deferred)

**Goal**: Three MCP tools (`export`, `clean`, `ingest`) available to WorkBuddy agent.

**Files**:
- `packages/mcp_server/src/agent_memory_mcp/server.py` — MCP server entry, tool registration
- `packages/mcp_server/src/agent_memory_mcp/tools/export_tool.py` — in-process CLI call
- `packages/mcp_server/src/agent_memory_mcp/tools/clean_tool.py` — LLM cleaning
- `packages/mcp_server/src/agent_memory_mcp/tools/ingest_tool.py` — WeKnora HTTP upload
- `packages/mcp_server/src/agent_memory_mcp/llm_client.py` — OpenAI-compatible API client
- `packages/mcp_server/src/agent_memory_mcp/weknora_client.py` — WeKnora REST client
- `packages/mcp_server/src/agent_memory_mcp/prompts/clean_prompt.md` — cleaning prompt
- `packages/mcp_server/src/agent_memory_mcp/upload_state.py` — upload tracking
- `config/mcp_config.yaml` — LLM + WeKnora endpoint config

**Design decisions** (confirmed with user):
- MCP calls CLI core via in-process `import` (not subprocess) for performance
- Three separate tools (export/clean/ingest) for independent retry and granular control
- MCP only does Source-KB ingestion; Auto-Wiki distillation is a separate cron job
- `upload_state.json` tracks per-file upload status with retry count (max 3)

---

## Testing Strategy

- **Fixtures**: hand-crafted minimal `.jsonl` files and mock SQLite DBs in `tests/fixtures/`
- **Unit tests**: assembler turn-splitting, redactor pattern matching, state incremental logic, gap detection
- **Integration test**: run CLI against real WorkBuddy data with `--limit 1`, verify raw JSON structure
- **No LLM/HTTP tests**: MCP server tools tested with mocked LLM and WeKnora clients
