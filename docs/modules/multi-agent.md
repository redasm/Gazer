# 多 Agent 协同系统

当单轮对话无法满足复杂任务需求时，Gazer 可自动将任务分配给多个 Worker Agent 并行执行，由 Planner Agent 统一规划、监控与汇总。

## 模块职责

- **自动分流**：根据任务复杂度四维评分决定走单 Agent 还是多 Agent。
- **智能脑路由**：所有 LLM 调用通过 `BrainHint` 声明意图，由 `DualBrainRouter` 统一决策快/慢脑。
- **DAG 任务编排**：Planner 将目标拆解为有向无环图，Worker 按依赖关系并行执行。
- **共享黑板**：Worker 将完整结果写入 Blackboard，避免大量内容在对话历史中来回传递。
- **自适应恢复**：Worker 执行出错时自动切换慢脑重试，超出能力则升级给 Planner。

## 关键代码

- 脑路由：`src/multi_agent/brain_router.py`
- 复杂度评估：`src/multi_agent/assessor.py`
- 双脑适配：`src/multi_agent/dual_brain.py`
- 任务图：`src/multi_agent/task_graph.py`
- 通信层：`src/multi_agent/communication.py`（AgentMessageBus + Blackboard）
- Worker：`src/multi_agent/worker_agent.py`
- 动态池：`src/multi_agent/agent_pool.py`
- Planner：`src/multi_agent/planner.py`
- 统一入口：`src/multi_agent/runtime.py`
- Agent 集成：`src/agent/adapter.py`（`process_auto`、`process_multi_agent`）

## 核心组件

### BrainHint 与 DualBrainRouter

调用方通过 `BrainHint` 声明意图而非硬编码选择脑：

| 维度 | 说明 |
|------|------|
| `latency_critical` | 需要低延迟 → 强制快脑 |
| `quality_critical` | 需要高质量 → 慢脑 |
| `reasoning_depth` | 1=反应式、2=中等、3=深度规划 → depth≥2 用慢脑 |

优先级：`latency_critical` > `quality_critical / depth≥3` > `depth==2` > 默认快脑。

### TaskComplexityAssessor

在真正规划之前用快脑做轻量评估（<200ms），四维打分：

1. 任务可拆分为 3+ 独立子任务？
2. 需要从多个来源搜集信息？
3. 用户可接受 30s+ 等待换取更好结果？
4. 子任务间依赖少？

| 分数 | 决策 | Worker 数 |
|------|------|-----------|
| 0-1 | 单 Agent | - |
| 2 | 多 Agent | 2 |
| 3 | 多 Agent | 4 |
| 4 | 多 Agent | 用户配置上限 |

评估失败时保守降级到单 Agent。

### TaskGraph（任务 DAG）

- 支持动态子任务注入与依赖改写。
- 上游任务永久失败时下游自动标记 BLOCKED。
- 状态变更通过 `asyncio.Event` 通知监控循环，避免轮询。

### WorkerAgent

- **Work Stealing**：从就绪队列原子领取任务。
- **正常执行**：`BrainHint(reasoning_depth=1)` → 快脑。
- **错误恢复**：`BrainHint(quality_critical=True, reasoning_depth=3)` → 切慢脑自适应重试。
- **交错思考**：每次工具调用后评估结果质量，不足则继续。
- **升级机制**：超出能力标记 `need_planner=True`，Planner 介入处理。
- Worker 间不直接通信，通过 Blackboard 共享状态。

### PlannerAgent

- 慢脑生成任务 DAG（含目标/输出格式/工具指引/任务边界四要素）。
- 并发运行监控循环与消息循环。
- 处理 Worker 升级请求（修订指令/拆分子任务/标记失败）。
- 从 Blackboard 读取完整结果，慢脑综合生成最终答案。
- 将规划经验写入 OpenViking 记忆，支持自进化。

### AgentPool

- 动态扩缩容，`min_agents` ≤ 当前数 ≤ `max_agents`。
- 就绪任务多于空闲 Worker 时自动扩容。
- 长时间无任务时自动缩容。
- 所有 Worker 共享同一 DualBrain 实例。

## 运行流程

```
用户输入
    │
    ▼ (process_auto)
TaskComplexityAssessor.assess()  ← 快脑，<200ms
    │
    ├── score < 2 ──→ process_message()（单 Agent 路径，不变）
    │
    └── score >= 2 ──→ process_multi_agent(goal, worker_hint)
          │
          ▼
    MultiAgentRuntime.execute(goal)
          │
          ├── PlannerAgent._plan()        ← 慢脑规划
          ├── _build_task_graph()          ← 构建 DAG
          ├── AgentPool.start()           ← 启动 Worker
          ├── _monitor_loop() + _message_loop()  ← 并发监控
          ├── _aggregate_results()        ← 慢脑汇总
          └── _save_planning_memory()     ← 自进化记忆
```

## 配置

```yaml
multi_agent:
  allow_multi: false   # 是否允许系统自动路由到多 Agent
  max_workers: 5       # Worker 数量上限（系统按任务评分动态决定实际数量）
```

Web 管理台路径：Settings → Smart Collaboration。

## 设计约束

- 多 Agent 模块位于 `src/multi_agent/`，与现有 Agent 系统完全隔离。
- `process_message()` 单 Agent 路径保持零修改。
- AgentMessageBus 与现有 `src/bus/queue.py` 完全独立。
- Token 消耗约为普通对话 15 倍，评估器自动门控低复杂度任务。

## 排障建议

- 未触发多 Agent：检查 `multi_agent.allow_multi` 是否为 `true`，快脑 Provider 是否可用。
- Worker 卡住：检查 `max_iterations` 上限和工具注册表可用性。
- 规划失败：检查慢脑 Provider 连通性和输出格式解析日志。
- 结果不完整：检查 Blackboard 写入和 Planner 汇总日志。
