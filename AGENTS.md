# AGENTS.md

> 入口路由文件,不是百科全书。保持 50-200 行,详细规则拆到 `docs/` 下的专题文档。
> 来源:Learn Harness Engineering 第 2、4 讲。

## 项目概览

Python 3.11+ CLI 工具 + MCP Server，从 WorkBuddy/OpenCode 本地数据导出 agent 会话，重组 turn，PII 脱敏，输出 raw JSON 供 WeKnora 知识库入库。uv workspace monorepo（shared/cli/mcp_server 三包），pytest 测试，无数据库写入（只读源数据）。

## 快速开始

```bash
uv sync --all-packages    # 安装依赖（含 dev group）
uv run pytest tests/ -v   # 运行测试
uv run agent-memory-exporter export --source workbuddy --limit 3  # 试导出 3 个会话
uv run agent-memory-exporter status   # 查看导出状态
```

## 硬约束

1. CLI 只读源数据，绝不修改 workbuddy.db 或 projects/*.jsonl
2. 所有 raw JSON 必须符合 schemas/raw_session.schema.json
3. PII 脱敏在 CLI 阶段完成（redaction_rules.yaml），不依赖 LLM
4. CLI 不调用 LLM、不调用 HTTP API——那是 MCP Server 的职责
5. SourceAdapter 协议是扩展点：新数据源只需实现 detect/list_sessions/read_session
6. uv sync --all-packages 必须成功，pytest 36 个测试必须全绿
7. 增量导出必须基于 state.json，不能全量重跑除非 --full
8. export 命令的 --output 目录下不提交到 git（.gitignore 已排除 agent_export/）

## 必需文件

| 文件 | 何时读 | 何时写 |
|------|--------|--------|
| `feature_list.json` | 每轮会话开始(选任务) | 每轮会话结束(更新状态) |
| `claude-progress.md` | 每轮会话开始(恢复状态) | 每轮会话结束(记录进度) |
| `init.sh` | 每轮会话开始(运行) | 不写,只运行 |
| `clean-state-checklist.md` | 每轮会话结束前(逐项确认) | 不写,只读 |
| `evaluator-rubric.md` | 功能完成后、宣布完成前(打分) | 打分后填入结果 |
| `session-handoff.md` | 交接时(见下方触发条件) | 交接时(见下方触发条件) |

## 工作循环

### 开工(每轮会话开始)

1. `pwd` — 确认当前在 `D:\agent_memory_exporter`
2. 读 `claude-progress.md` — 恢复持久状态
3. 读 `feature_list.json` — 选择优先级最高的未完成功能
4. `git log --oneline -5` — 看最近提交
5. `./init.sh` — 标准化启动
6. 跑 `uv run pytest tests/ -q` — 确认基线没坏
7. 如果基线已坏,**先修基线**,不要在坏状态上叠新功能
8. 选择一个未完成功能,WIP=1,只围绕它工作

### 收尾(每轮会话结束前)

1. 跑 `make check` — 验证全绿
2. 更新 `claude-progress.md` — 记录进度
3. 更新 `feature_list.json` — 更新功能状态
4. 清理临时工件 — 删 agent_export_test*、__pycache__
5. 跑 `clean-state-checklist.md` — 逐项确认
6. `git commit` — 提交干净状态
7. 必要时写 `session-handoff.md` — 较长会话才需要

### 何时写 session-handoff.md

以下任一条件满足时,必须写 `session-handoff.md`:

- 功能未完成(state 仍是 `active`),但会话即将结束
- 本轮做了基础设施或 harness 层面的改动(改了 AGENTS.md、init.sh、Makefile、docs/ 等)
- 有已知缺陷或未验证路径需要传给下一轮
- 换人或换 agent 接手

以上条件都不满足时(功能已完成、无已知缺陷、无 harness 改动),可以跳过。

## 完成定义

一个功能只有在以下条件**都**满足时才算完成:

- 目标行为已经实现
- `uv run pytest tests/ -v` 全部通过
- 证据记录在 `feature_list.json` 或 `claude-progress.md`
- 仓库仍然能按标准启动路径重新开始工作(`./init.sh` 能跑通)

## 验收流程(宣布完成前必做)

功能完成后,在宣布完成之前,必须按以下顺序执行:

1. 跑 `make check`,贴出完整输出
2. 打开 `evaluator-rubric.md`,逐维度打分(0-2),填入备注和结论
3. 打开 `clean-state-checklist.md`,逐项确认
4. 两个都通过后,才能更新 `feature_list.json` 的 state 为 `passing`,填 evidence
5. agent 不能自己改 `passing`,只有验证命令通过才能改

如果 evaluator-rubric 的结论是 Block 或 Revise,不许宣布完成,先修问题。

## 专题文档(按需阅读,不要一次全读)

- `docs/architecture.md` — 架构决策记录,改架构时读
- `docs/data-sources.md` — WorkBuddy/OpenCode 数据结构,改 adapter 时必读
- `docs/implementation-plan.md` — 5 阶段实现计划,选下一个功能时读

## 指令维护原则

- 每条指令标明来源("为什么加这条规则")、适用条件("什么时候需要")、过期条件("什么时候可删")
- 定期审计,删掉过时的、冗余的、矛盾的条目
- 入口文件超过 200 行就考虑拆分到专题文档
- 重要信息放文件顶部或底部,不要放中间(LLM 中间迷失效应,第 4 讲)
