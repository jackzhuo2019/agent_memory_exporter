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
- `packages/mcp_server/src/agent_memory_mcp/tools/clean_tool.py` — LLM cleaning (打 quality 标签: high/medium/low/trash; trash 不入库)
- `packages/mcp_server/src/agent_memory_mcp/tools/ingest_tool.py` — WeKnora HTTP upload (只入库 high/medium)
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
- clean 工具给每条 md 打 quality 标签 (high/medium/low/trash)，只有 high/medium 入 Source-KB

---

### Phase 6: Auto-Wiki Distillation (deferred)

**Goal**: 定时 cron 任务从 Source-KB 提取高频主题，蒸馏为 wiki 条目写入 Auto-Wiki KB，带 provenance 溯源。

**核心设计决策** (confirmed with user):

1. **增量蒸馏** (非全量): `distill_state.json` 记忆已处理的 Source-KB 条目 ID，每周只处理新增条目。LLM token 消耗与新增量成正比，不随总量增长。

2. **topic_id 稳定化**: LLM 从主题簇提取 3-5 个关键词 → slugify → `weknora-api-config`。同一主题不同周聚类，关键词集合相近 → slug 一致。LLM 不直接生成 topic_id，避免漂移导致重复 wiki 条目。

3. **覆盖策略: 比较后选优**: 已有主题被重新蒸馏时，LLM 比较旧 wiki vs 新 wiki，选更完整/更准确的版本。新版本更优 → delete 旧 + create 新 (distill_version+1)。旧版本更优 → 跳过 (provenance 仍记录新片段)。

4. **检索优先级**: Agent 检索时 Auto-Wiki KB 优先 → 命中则返回精炼摘要 → 未命中 → Source-KB 兜底。

5. **蒸馏后 Source-KB 不修改**: 原始会话永久保留，可追溯。

6. **一次性任务处理**: clean 工具 (F04) 阶段打 quality 标签，trash 不入库 Source-KB。

**Files**:
- `packages/mcp_server/src/agent_memory_mcp/distill/__init__.py`
- `packages/mcp_server/src/agent_memory_mcp/distill/distiller.py` — 蒸馏主逻辑
- `packages/mcp_server/src/agent_memory_mcp/distill/topic_cluster.py` — 主题聚类 (向量化 + 相似度比较)
- `packages/mcp_server/src/agent_memory_mcp/distill/wiki_writer.py` — 写入 Auto-Wiki KB (冲突检测 + 比较后选优)
- `packages/mcp_server/src/agent_memory_mcp/distill/provenance.py` — provenance 管理 (去重合并)
- `packages/mcp_server/src/agent_memory_mcp/distill/distill_state.py` — distill_state.json 读写
- `packages/mcp_server/src/agent_memory_mcp/prompts/distill_prompt.md` — 蒸馏 prompt (可热更新)
- `packages/mcp_server/src/agent_memory_mcp/scheduler.py` — cron 定时触发

**distill_state.json 结构**:
```jsonc
{
  "schema_version": "1.0.0",
  "last_distill_ts": "2026-08-21T12:00:00Z",
  "source_kb_id": "0dc7bd21-...",
  "wiki_kb_id": "auto-wiki-kb-id",
  "processed_knowledge_ids": ["kb-001", "kb-042", "kb-088"],
  "wiki_topics": {
    "weknora-api-config": {
      "wiki_knowledge_id": "wiki-xxx",
      "source_knowledge_ids": ["kb-001", "kb-042", "kb-088"],
      "distill_version": 2,
      "last_distill_ts": "2026-08-21T...",
      "content_hash": "sha256:..."
    }
  },
  "distill_count": 2
}
```

**蒸馏流程**:
```
1. 读 distill_state.json
2. list_knowledge(Source-KB) → 全部条目
3. 过滤: 新条目 = 全部 - processed_knowledge_ids → 如果为空则跳过
4. 向量化新条目 → 与已有 wiki_topics 向量做相似度比较 → 聚类
5. LLM 提取关键词 → slugify → topic_id (确定性)
6. 对每个主题:
   a. 新主题 → LLM 生成 wiki → create → Auto-Wiki KB
   b. 已有主题 → 读旧 wiki + 新片段 → LLM 合并 → 比较后选优 → 覆盖或跳过
7. 合并 provenance (去重) → 更新 distill_state.json
```

**Verification**:
- wiki 条目数 > 0
- 每个 wiki 条目有 provenance 字段包含来源 session ID
- 重复运行蒸馏不产生重复 wiki 条目 (topic_id 稳定)
- distill_state.json 的 processed_knowledge_ids 只增不减

---

## Testing Strategy

- **Fixtures**: hand-crafted minimal `.jsonl` files and mock SQLite DBs in `tests/fixtures/`
- **Unit tests**: assembler turn-splitting, redactor pattern matching, state incremental logic, gap detection
- **Integration test**: run CLI against real WorkBuddy data with `--limit 1`, verify raw JSON structure
- **No LLM/HTTP tests**: MCP server tools tested with mocked LLM and WeKnora clients
