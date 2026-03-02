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

## 接入建议

- 新增认知步骤时，优先遵循 `WorkingContext -> Step -> WorkingContext` 的纯函数式风格。
- 新增记忆后端时，仅扩展 `MemoryPort` 实现，避免穿透到 Soul 上层逻辑。
- 调整人格演化策略时，先确认是否会破坏 `IdentityConstitution` 约束。
