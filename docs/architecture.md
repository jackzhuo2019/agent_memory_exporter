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
- `list_knowledge(kb_id, page, page_size)` — list knowledge in a KB (paginated)
- `get_knowledge(knowledge_id)` — get knowledge detail
- `delete_knowledge(knowledge_id)` — delete knowledge

The `ingest` tool calls the HTTP API directly (`POST /knowledge-bases/{kb_id}/knowledge/file`) rather than going through the MCP server, to avoid double-hop.

### Two-Layer Knowledge Base

| Layer | KB | Content | Characteristics |
|-------|-----|---------|----------------|
| Raw | Source-KB | All `processed/*.md` session files | Full text, high volume, high update frequency, fully traceable |
| Distilled | Auto-Wiki KB | Distilled wiki entries by topic | Small volume, high quality, low update frequency, per-topic aggregation |

Both KBs live in WeKnora. The Source-KB is written by the `ingest` tool (F05). The Auto-Wiki KB is written by the distillation cron job (F06).

## Auto-Wiki Distillation (F06)

Auto-Wiki distillation is a **separate cron job** (not an MCP tool). It reads Source-KB, clusters similar content, generates distilled wiki entries, and writes them to a separate Auto-Wiki KB.

### Distillation Flow (Incremental)

```
每周 cron 触发:

1. 读 distill_state.json
   - processed_knowledge_ids: 已蒸馏的 Source-KB 条目集合
   - wiki_topics: 已有 wiki 主题索引 (topic_id → metadata)

2. list_knowledge(Source-KB) → 全部条目

3. 过滤: 新条目 = 全部条目 - processed_knowledge_ids
   - 如果新条目数 == 0 → 跳过

4. 对新条目做聚类:
   a. 向量化新条目 (title + content 摘要)
   b. 与已有 wiki_topics 的向量做相似度比较
   c. 相似度 > 阈值 (0.85) → 归入已有主题
   d. 不相似 → 新主题

5. topic_id 生成 (关键词 slug):
   - LLM 从主题簇提取 3-5 个关键词
   - slugify: "WeKnora API 配置方法" → "weknora-api-config"
   - 同一主题不同周聚类 → 关键词集合相近 → slug 稳定
   - LLM 不直接生成 topic_id (避免漂移)

6. 对每个主题:
   a. 新主题 → LLM 生成 wiki 条目 → create → Auto-Wiki KB
   b. 已有主题 → 读旧 wiki + 新片段 → LLM 合并 → 比较后选优:
      - 新版本更优 → delete 旧 + create 新 → distill_version + 1
      - 旧版本更优 → 跳过 (provenance 仍记录新片段)
      - 合并 provenance (去重)

7. 更新 distill_state.json
```

### distill_state.json

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

### wiki 条目结构

```jsonc
{
  "title": "WeKnora API 配置方法",
  "content": "# WeKnora API 配置\n\n## 基本配置\n...\n## 认证\n...",
  "metadata": {
    "topic_id": "weknora-api-config",
    "provenance": [
      "session_id:2d9ac6f2-...",
      "session_id:8b8ee662-..."
    ],
    "last_distilled_at": "2026-08-21T12:00:00Z",
    "source_count": 3,
    "distill_version": 2
  }
}
```

### 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 蒸馏增量 | `distill_state.json` 记忆已处理条目 | 避免每周全量重复计算，LLM token 消耗只与新增量成正比 |
| topic_id 生成 | 关键词 slug（LLM 提取关键词 → slugify） | 同一主题不同周聚类关键词集合相近 → slug 稳定，不由 LLM 直接生成 ID 避免漂移 |
| wiki 覆盖策略 | 比较后选优（旧 vs 新 → LLM 判断） | 避免较差版本覆盖较好版本，每次需要 2x token（读旧+生成新+比较） |
| 蒸馏后 Source-KB | 不修改 | 原始会话永久保留，可追溯 |
| 一次性任务处理 | clean 工具标记 quality，trash 不入库 | 在 F04 clean 阶段过滤，不进入 Source-KB |

### 实现位置

```
packages/mcp_server/src/agent_memory_mcp/
├── tools/
│   ├── export_tool.py      # F03
│   ├── clean_tool.py       # F04 (打 quality 标签: high/medium/low/trash)
│   └── ingest_tool.py      # F05 (只入库 high/medium)
├── distill/                 # F06
│   ├── __init__.py
│   ├── distiller.py        # 蒸馏主逻辑
│   ├── topic_cluster.py    # 主题聚类 (向量化 + 相似度)
│   ├── wiki_writer.py      # 写入 Auto-Wiki KB (冲突检测 + 比较后选优覆盖)
│   ├── provenance.py       # provenance 管理 (去重合并)
│   └── distill_state.py    # distill_state.json 读写
├── prompts/
│   ├── clean_prompt.md     # F04 清洗 prompt
│   └── distill_prompt.md   # F06 蒸馏 prompt (可热更新)
└── scheduler.py             # cron 定时触发
```

## Agent Retrieval Priority

Agent 检索时的优先级：

```
1. hybrid_search(Auto-Wiki KB, query, match_count=3)
   → 命中 → 返回精炼摘要 (token 少, 质量高)
   → 未命中 ↓

2. hybrid_search(Source-KB, query, match_count=5)
   → 返回原始会话片段 (全量, 可追溯)

3. 合并结果返回给 agent
```

Auto-Wiki 优先：如果精炼 wiki 中有答案，agent 不需要读原始会话，节省 token。Source-KB 兜底：wiki 未覆盖的主题仍可从原始会话中检索。
