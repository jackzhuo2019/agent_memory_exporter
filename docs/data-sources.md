# Data Source Reference

Real-world data structures discovered by inspecting `~/.workbuddy/` and `~/.local/share/opencode/opencode.db`.

## WorkBuddy

### workbuddy.db — `sessions` table

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- == conversation-id in jsonl filename
    cwd TEXT NOT NULL,             -- working directory, escaped to projects/<cwd-hash>/
    user_id TEXT NOT NULL,
    title TEXT,                    -- user-visible session title (may contain garbled encoding)
    custom_title TEXT,
    status TEXT NOT NULL DEFAULT 'Completed',  -- 'completed', etc.
    created_at INTEGER NOT NULL,   -- Unix epoch ms
    updated_at INTEGER NOT NULL,   -- Unix epoch ms (used for incremental export)
    deleted_at INTEGER,            -- soft delete timestamp
    is_playground INTEGER NOT NULL DEFAULT 0,
    source_mode TEXT,
    is_background_automation INTEGER,
    mode TEXT,                     -- 'craft', etc.
    model TEXT,                    -- 'hy3', 'custom-local:glm-5.2', etc.
    expert_id TEXT,
    expert_locale TEXT,
    expert_runtime_identity TEXT,
    expert_marketplace TEXT,
    permission_mode TEXT,
    last_activity_at INTEGER,
    use_sandbox_cli INTEGER,
    project_id TEXT,
    plugin_context_json TEXT,
    last_user_prompt_expert_selection TEXT
);
```

### projects/<cwd-hash>/<conversation-id>.jsonl

Each line is a JSON object (event). Observed `type` values:

#### `message` (role: user | assistant)

```jsonc
{
  "id": "95057b38-...",
  "timestamp": 1781675942999,          // Unix epoch ms (number)
  "type": "message",
  "role": "user",                       // or "assistant"
  "content": [
    {"type": "input_text", "text": "..."}
  ],
  // assistant messages may have:
  "providerData": {"agent": "requirement-analyst"},
  "sessionId": "2d9ac6f2-...",
  "cwd": "c:\\Users\\Administrator\\WorkBuddy\\2026-06-17-13-58-58"
}
```

**Note**: `content` can be a list of dicts (OpenAI format) or a string (legacy). The adapter must handle both.

#### `function_call`

```jsonc
{
  "id": "f61cdea2-...",
  "parentId": "5920561e-...",           // parent message id
  "timestamp": 1781675955598,
  "type": "function_call",
  "providerData": {"reasoning": "...", "extra_fields": null},
  "callId": "call_xxx",
  "name": "Read",                        // tool name
  "arguments": "{\"file_path\": \"...\"}", // JSON string
  "sessionId": "...",
  "message": "...",                      // optional context
  "cwd": "..."
}
```

#### `function_call_result`

```jsonc
{
  "id": "...",
  "parentId": "f61cdea2-...",           // links to function_call id
  "timestamp": 1781675955600,
  "type": "function_call_result",
  "callId": "call_xxx",                 // matches function_call.callId
  "result": "...",                       // tool output (string or JSON)
  "sessionId": "...",
  "cwd": "..."
}
```

#### `reasoning`

```jsonc
{
  "id": "...",
  "parentId": "...",
  "timestamp": 1781675955000,
  "type": "reasoning",
  "content": "...",                     // chain-of-thought text
  "sessionId": "...",
  "cwd": "..."
}
```

#### `file-history-snapshot`

Filesystem state snapshot. Large, optional to keep in export. Can be filtered out by default.

#### `ai-title`

```jsonc
{
  "timestamp": "1783560278258",         // NOTE: string, not number!
  "type": "ai-title",
  "aiTitle": "...",
  "sessionId": "...",
  "cwd": "..."
}
```

Metadata only — not part of conversation flow. Used to update session title.

### projects/<cwd-hash>/agent-*.jsonl

Sub-agent conversations. Same event format as main conversation, but:
- Has its own `sessionId` (different from main conversation)
- `parentId` on the first event links back to a message in the main conversation
- Should be attached to the parent turn (the turn containing the parent message)

### cwd → directory name escaping

Observed mapping:
```
C:\Users\Administrator\WorkBuddy\2026-06-17-13-58-58
→ c-Users-Administrator-WorkBuddy-2026-06-17-13-58-58
```

Rule: lowercase the drive letter, replace `\` with `-`. Fallback: traverse all `projects/` subdirectories and match the `cwd` field inside the first line of the jsonl.

### Other WorkBuddy files (NOT conversation data)

| Path | Content | Use |
|------|---------|-----|
| `traces/<PID>/trace_*.json` | OpenTelemetry spans (tool call timing) | Not used — performance data only |
| `workspace/sessions/<uuid>/` | Filesystem snapshots (`fs/`, `modify_backup/`) | Not used |
| `memory/<uuid>_memory.md` | WorkBuddy agent's persistent memory | Could be exported separately in future |
| `sessions/<PID>.json` | Runtime process heartbeat | Not used |
| `app/sessions.json` | Session metadata index | Not used (DB is canonical) |

---

## OpenCode

### opencode.db — key tables

```sql
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    directory TEXT NOT NULL,
    title TEXT NOT NULL,
    model TEXT,
    agent TEXT,
    time_created INTEGER NOT NULL,   -- Unix epoch ms
    time_updated INTEGER NOT NULL,
    -- ... (cost, tokens_*, metadata, etc.)
);

CREATE TABLE session_message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,              -- 'message', 'tool', etc.
    seq INTEGER NOT NULL,            -- ordering
    data TEXT NOT NULL,              -- JSON
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);

CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,        -- FK to session_message.id
    session_id TEXT NOT NULL,
    data TEXT NOT NULL,              -- JSON: {role, content, tool_name, ...}
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);

CREATE TABLE session_input (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    delivery TEXT NOT NULL,
    time_created INTEGER NOT NULL
);
```

**Read path**: `session` → `session_message` (ordered by `seq`) → `part` (ordered by `time_created`), parse `part.data` JSON.

---

## WeKnora

### MCP Server module

Located at: `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Lib\site-packages\weknora_mcp_server.py`

### HTTP API

Base URL: `http://<host>:8088/api/v1`
Auth: `Authorization: Bearer <WEKNORA_API_KEY>`

Key endpoints (discovered from `weknora_mcp_server.py`):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/knowledge-bases/{kb_id}/knowledge/file` | Upload file (multipart) |
| POST | `/knowledge-bases/{kb_id}/knowledge/url` | Create knowledge from URL |
| GET | `/knowledge-bases/{kb_id}/knowledge` | List knowledge (paginated) |
| GET | `/knowledge/{knowledge_id}` | Get knowledge detail |
| DELETE | `/knowledge/{knowledge_id}` | Delete knowledge |
| GET | `/knowledge-bases` | List knowledge bases |
| POST | `/knowledge-bases` | Create knowledge base |
| POST | `/knowledge-bases/{kb_id}/hybrid-search` | Semantic + keyword search |

File upload uses `multipart/form-data` with `file` field and `enable_multimodel` parameter.
