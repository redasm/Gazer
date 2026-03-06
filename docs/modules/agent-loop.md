# Agent 循环与调度

Agent 层由 `GazerAgent + AgentLoop + MultiAgentRuntime` 组成：

- `GazerAgent` 负责 Provider、Context、Memory 注入，以及单 / 多 Agent 自动分流。
- `AgentLoop` 负责单轮对话执行。
- `MultiAgentRuntime` 负责复杂任务的规划、Worker 调度与结果汇总。

## 关键代码

- 适配与初始化：`src/agent/adapter.py`
- 主循环：`src/agent/loop.py`
- 多 Agent 运行时：`src/multi_agent/runtime.py`
- 上下文构建：`src/agent/context.py`
- Turn Hook：`src/agent/turn_hooks.py`

## 自动分流（process_auto）

`process_auto()` 是用户消息的统一入口，根据任务复杂度自动决定执行路径：

1. 检查 `multi_agent.allow_multi` 配置和快脑 Provider 可用性。
2. 若满足条件，用 `TaskComplexityAssessor` 做四维快速评估（<200ms）。
3. 评分 ≥ 2 → 路由到 `process_multi_agent()`（多 Agent 路径）。
4. 评分 < 2 或评估失败 → 路由到 `process_message()`（单 Agent 路径）。

```
process_auto(content, sender)
    │
    ├── allow_multi && _fast_provider?
    │      └── TaskComplexityAssessor.assess(content)
    │            ├── score >= 2 → process_multi_agent(content, worker_hint)
    │            └── score < 2  → process_message(content, sender)
    │
    └── process_message(content, sender)  [默认]
```

详见：[多 Agent 协同系统](./multi-agent.md)

## 单轮处理链路（AgentLoop）

1. 从 `MessageBus` 消费 `InboundMessage`。
2. 构建上下文（系统提示词、记忆上下文、技能信息、历史对话）。
3. 选择快脑/慢脑模型并请求 LLM。
4. 若包含 tool calls，则执行工具并回填结果。
5. 输出 `OutboundMessage` 到总线。
6. 记录 usage、trajectory、policy 诊断与记忆持久化。

## 策略与防护

- 工具策略管线：`tool_policy_pipeline.py`
- 人格信号联动策略：`persona_tool_policy.py`
- 速率限制与重试预算：`runtime/rate_limiter.py`、`runtime/resilience.py`
- 会话与轨迹：`session_store.py`、`trajectory.py`

## 多 Agent 调度（Runtime）

- 基于 `TaskComplexityAssessor` 自动判断是否启用多 Agent。
- 由 Planner 生成任务 DAG，Worker 池按需扩缩容执行。
- 结果写入 Blackboard，最终由 Planner 汇总为单个回复。

## 常见配置

- `models.router.*`（路由策略与降级）
- `multi_agent.allow_multi`（启用多 Agent 自动分流）
- `multi_agent.max_workers`（Worker 数量上限）

配置文件：`config/settings.yaml`

## 排障建议

- 响应慢：先看路由是否降级、工具是否阻塞、并发是否打满。
- 工具连环失败：检查 `ToolRegistry` 熔断状态与策略拒绝原因。
- 多 Agent 行为异常：检查 Planner 是否成功分解任务，以及 Worker 数上限是否过低。
- 未触发多 Agent：检查 `multi_agent.allow_multi` 是否为 `true`，快脑 Provider 是否可用。
