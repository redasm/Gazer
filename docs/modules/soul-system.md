# Soul 系统（认知/情感/人格）

Soul 模块负责“像谁、如何思考、如何保持连续性”，是 Gazer 的人格与认知核心。

## 关键代码

- 工作上下文：`src/soul/memory/working_context.py`
- 记忆端口抽象：`src/soul/memory/memory_port.py`
- 上下文预算：`src/soul/cognitive/context_budget_manager.py`
- 主动推断：`src/soul/cognitive/proactive_inference_engine.py`
- 人格向量：`src/soul/personality/personality_vector.py`
- 人格演化：`src/soul/personality/evolution_service.py`
- 宪法约束：`src/soul/personality/identity_constitution.py`

## 当前实现重点

### 1) 三槽位不可变 WorkingContext

`WorkingContext` 将上下文拆分为：

- `user_context`
- `agent_context`
- `session_context`

并通过 `with_update()` 生成新快照，避免原地修改导致状态污染。

### 2) MemoryPort 依赖反转

Soul 不直接依赖具体存储后端，统一依赖 `MemoryPort` 抽象。

- `OpenVikingMemoryPort`
- `InMemoryMemoryPort`
- `EmotionAwareMemoryPort`

### 3) 上下文预算与 Lost-in-the-Middle

`ContextBudgetManager` 按 head/middle/tail 策略组装 prompt，优先保留高价值信息：

- Head：人格与情绪
- Middle：用户背景/会话记忆/历史对话
- Tail：当前用户输入

### 4) 主动推断

`ProactiveInferenceEngine` 在足够轮次后推断潜在需求（如情绪支持、能量下降），并把 hint 注入 `agent_context`。

### 5) 人格演化边界

`IdentityConstitution` 对人格演化做双层约束：

- 硬边界：OCEAN 数值范围
- 软边界：基于宪法原则的语义校验

防止系统滑向极端迎合或失真人格。

## 与重构文档对齐

当前实现与以下设计文档已基本对齐：

- `doc/soul_architecture_reform.md`
- `doc/soul_architecture_reform_patch_v1.2.md`

新增能力已落地到 `working_context`、`memory_port`、`context_budget_manager`、`proactive_inference_engine`、`identity_constitution` 等路径。

## 工具列表注入（Available Tools）

Soul 在组 prompt 时会把当前可用的工具定义以 `Available Tools: [...]` 注入到 `agent_context`，供 LLM 决定是否调用工具。工具列表来自 **与 AgentLoop 共用的** `ToolRegistry`（由 GazerAgent 注入）。

若会话里出现 **Available Tools: []**，说明此时 `tool_registry.get_definitions()` 返回了空列表，即 Soul 拿到的工具列表为空。常见原因：

1. **未走完整 Brain 启动**：工具是在 `GazerBrain.start()` → `_setup_tools()` 里注册的；若只起了 Admin API 或测试脚本、未启动 Brain，或 Brain 在 `_setup_tools()` 完成前就处理了首条消息，则 registry 可能仍为空。
2. **多进程/多实例**：若消息由未挂载同一 Brain 的进程处理（例如单独起的 API 进程且未通过 IPC 把请求转给 Brain），该进程内的 agent 可能从未执行过 `_setup_tools()`，工具列表为空。

排查建议：确认启动日志中有 `Registered N tools.`（N > 0），且处理该会话的进程就是完成 `_setup_tools()` 的 Brain 进程。Soul 在检测到空工具列表时会打一条 warning 日志，便于定位。

## 接入建议

- 新增认知步骤时，优先遵循 `WorkingContext -> Step -> WorkingContext` 的纯函数式风格。
- 新增记忆后端时，仅扩展 `MemoryPort` 实现，避免穿透到 Soul 上层逻辑。
- 调整人格演化策略时，先确认是否会破坏 `IdentityConstitution` 约束。
