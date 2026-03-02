# 多 Agent 协同架构

## 设计动机

单轮 Agent 适合简单查询和单步工具调用，但面对"需要并行收集信息、多步规划、跨领域综合"的复杂任务时存在瓶颈。多 Agent 协同系统在不修改现有单 Agent 链路的前提下，提供分治与并行能力。

## 架构分层

```
┌─────────────────────────────────────────────┐
│              GazerAgent (adapter)            │
│  process_auto() ─ 自动分流门控              │
├────────────────┬────────────────────────────┤
│  单 Agent 路径  │     多 Agent 路径           │
│  process_msg()  │  MultiAgentRuntime.execute()│
│  (现有链路不变)  │                            │
│                ├───────────────────────────┤
│                │       PlannerAgent         │
│                │  慢脑规划/监控/汇总          │
│                ├───────────────────────────┤
│                │       AgentPool            │
│                │  WorkerAgent × N           │
│                │  快脑执行 / 错误时切慢脑      │
│                ├───────────────────────────┤
│                │    TaskGraph (DAG)         │
│                │  任务状态机 + 事件通知        │
│                ├───────────────────────────┤
│                │  AgentMessageBus + Blackboard│
│                │  Agent 间通信 + 共享状态      │
└────────────────┴────────────────────────────┘
         ▲                    ▲
         │                    │
    ┌────┴────┐          ┌────┴────┐
    │DualBrain │         │BrainHint │
    │Router    │         │ 三维路由  │
    └─────────┘          └─────────┘
```

## 关键架构决策

### 1) 声明式脑路由取代硬编码选择

调用方不再直接选择 `fast_generate` / `slow_generate`，而是声明任务意图：

```python
hint = BrainHint(latency_critical=False, quality_critical=True, reasoning_depth=3)
result = brain.generate(prompt, hint=hint)
```

`DualBrainRouter` 根据三维决策矩阵自动选择最优 Provider，使路由逻辑集中管理、全局可观测。

### 2) 复杂度评估前置

`TaskComplexityAssessor` 在规划之前用快脑做四维打分（可并行性/信息广度/高价值/低依赖），200ms 以内给出分流决策。评估失败时降级到单 Agent，确保不会因评估器本身故障阻塞用户请求。

### 3) Worker 直写 Blackboard

Worker 的完整结果写入 Blackboard 而非对话消息。Planner 汇总时从 Blackboard 读取，避免上下文窗口爆炸。

### 4) 错误升级而非隐式吞没

Worker 执行失败后先尝试自适应恢复（切慢脑重试），仍失败则标记 `need_planner=True` 升级给 Planner。Planner 可选择：修订指令、拆分子任务、或标记永久失败。全程可追踪。

### 5) 现有链路零侵入

多 Agent 模块全部封装在 `src/multi_agent/` 内，对现有 `AgentLoop`、`MessageBus`、`ToolRegistry` 无修改。`process_auto()` 仅在分流处增加一个评估调用，评估器不可用时安全降级到原路径。

## 与现有组件的边界

| 组件 | 单 Agent 路径 | 多 Agent 路径 |
|------|-------------|-------------|
| `AgentLoop` | 直接使用 | 不使用（Worker 有独立执行循环） |
| `MessageBus` | 入站/出站 | 不使用（Agent 间通过 AgentMessageBus） |
| `ToolRegistry` | 工具执行 | 共享同一实例 |
| `MemoryManager` | 记忆读写 | Planner 写入规划记忆 |
| `DualBrain` | 快/慢脑 | 相同实例，通过 BrainHint 路由 |
| `ConfigManager` | 配置读取 | 读取 `multi_agent.*` |

## 并发模型

- Planner 启动两个并发协程：`_monitor_loop`（事件驱动的 DAG 监控）和 `_message_loop`（处理 Worker 消息）。
- AgentPool 中的每个 Worker 运行独立协程，通过 `asyncio.Lock` 做原子任务领取。
- TaskGraph 使用 `asyncio.Event` 通知状态变更，避免固定间隔轮询。

## 扩展点

- 接入外部 Agent 框架：实现 `WorkerAgent` 协议即可注册到 AgentPool。
- 自定义评估逻辑：继承 `TaskComplexityAssessor`，覆盖评分 prompt 或打分维度。
- 持久化规划记忆：当前写入 OpenViking，可扩展为专用知识库。
