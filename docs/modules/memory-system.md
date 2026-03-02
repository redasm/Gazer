# Memory 系统（OpenViking）

Gazer 当前记忆系统以 OpenViking 为核心持久层，`MemoryManager` 作为统一编排入口。

## 关键代码

- 统一管理器：`src/memory/manager.py`
- 后端适配：`src/memory/viking_backend.py`
- 启动预检：`src/memory/openviking_bootstrap.py`
- 召回/关系/情绪：`src/memory/recall.py`、`src/memory/relationships.py`、`src/memory/emotions.py`

## 数据与存储

默认数据根目录：`data/openviking`

典型文件：

- `memory_events.jsonl`：会话事件流
- `extraction_decisions.jsonl`：长期提炼决策日志
- `long_term/*.json`：分类长期记忆
- `pairing.json`：配对授权信息（安全模块共用）

## 运行流程

1. 启动阶段执行 OpenViking 配置与依赖预检。
2. 每轮 turn 由 `MemoryManager.save_entry()` 落库，并更新情绪/关系信息。
3. `get_companion_context()` 按 query 检索相关记忆，拼接上下文注入 LLM。
4. 召回统计（entity/semantic/reminder）被记录为可观测指标。

## OpenViking backend 机制

- 本地记录 JSONL 持久化。
- 可选将消息前推到 OpenViking client session。
- 达到阈值后自动 `commit_session`。
- 提供 FTS + hybrid search 能力给上层 recall。

## 关键配置

- `memory.context_backend.enabled`
- `memory.context_backend.mode`
- `memory.context_backend.data_dir`
- `memory.context_backend.commit_every_messages`
- `models.embedding.*`（可注入 OpenViking embedding 覆盖）

配置来源：`config/settings.yaml`

## 注意事项

- 记忆数据是运行时关键资产，迁移或清理前请先备份 `data/openviking`。
- 若启用 OpenViking 但包缺失，启动会在 preflight 阶段直接失败（符合 fail-fast 预期）。
- memory 变更建议补回归测试，不要仅靠手工对话验证。
