# 架构总览

Gazer 以 `src/` 为单一代码根，按能力域拆分模块，并由 Runtime 统一编排。

## 分层视图

- **Runtime**：生命周期、配置加载、组件装配。
- **Agent**：对话循环、策略执行、工具编排、自动分流。
- **Multi-Agent**：复杂任务的 Planner-Worker 并行协同。
- **Tools**：能力暴露与策略约束。
- **LLM**：模型供应商接入、路由与预算治理。
- **Memory/Soul**：长期记忆与人格认知。
- **Bus/Channels**：消息总线与多通道适配。

## 架构亮点

- **双脑 + BrainHint 路由**：声明式意图驱动的快/慢脑动态切换，路由逻辑集中管理。
- **多 Agent 协同**：复杂任务自动拆解为 DAG，Planner 规划、Worker 并行执行，对简单任务零开销。
- **策略驱动工具调用**：所有调用先过策略与安全门禁。
- **记忆 + 人格协同**：既保持事实连续性，也保持表达一致性。
- **多节点扩展能力**：通过卫星模式扩展系统边界。

## 运行主链路

1. `Channel` 接收用户消息。
2. `MessageBus` 入站排队并投递到 `AgentLoop`。
3. `process_auto()` 评估任务复杂度，自动分流：
   - 简单任务 → 单 Agent 路径（`AgentLoop` 构建上下文、请求 LLM、决定工具调用）。
   - 复杂任务 → 多 Agent 路径（`MultiAgentRuntime` 规划 DAG、Worker 并行执行、汇总结果）。
4. `ToolRegistry` 执行工具并进行策略审查。
5. 输出消息经 `MessageBus` 返回 Channel。

## 深入阅读

- [Runtime 内核](./runtime.md)
- [双脑架构](./dual-brain.md)
- [多 Agent 协同架构](./multi-agent.md)
- [Agent 与 Tool 调用链](./agent-tools.md)
- [LLM 路由与部署治理](./llm-routing.md)
- [Memory 与 Soul](./memory-soul.md)
- [Bus 与 Channels](./bus-channels.md)
