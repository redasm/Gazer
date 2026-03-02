# 双脑架构（Fast Brain / Deep Brain）

双脑架构是 Gazer 的核心设计之一：将"即时响应"与"深度推理"分离到不同 LLM Provider。

## 目标

- 降低普通对话时延（快脑 <1s）。
- 在复杂任务中保持决策质量（慢脑深度推理）。
- 避免所有请求都走高成本模型。
- 让调用方声明意图而非硬编码选择，路由逻辑集中可控。

## 双层实现

### 1) Agent 级快/慢脑（AgentLoop）

`AgentLoop` 在处理单轮对话时区分两条路径：

- 快脑路径 `_try_fast_brain()`：低延迟应答简单查询。
- 慢脑主路径 `_process_message()`：复杂推理、工具编排、高风险决策。

关键代码：`src/agent/loop.py`

### 2) BrainHint 声明式路由（多 Agent 系统）

多 Agent 系统中，所有 LLM 调用不再直接选择 Provider，而是通过 `BrainHint` 声明意图：

```python
@dataclass(frozen=True)
class BrainHint:
    latency_critical: bool = False   # 需要低延迟
    quality_critical: bool = False   # 需要高质量
    reasoning_depth: int = 1         # 1=反应式 2=中等 3=深度
```

`DualBrainRouter` 按三维决策矩阵选择 Provider：

| 条件 | 选择 |
|------|------|
| `latency_critical=True` | 快脑（无论其他维度） |
| `quality_critical=True` 或 `depth≥3` | 慢脑 |
| `depth==2` | 慢脑 |
| 其他 | 快脑 |

关键代码：`src/multi_agent/brain_router.py`

### 场景举例

| 场景 | BrainHint | 实际路由 |
|------|-----------|---------|
| Worker 正常执行 | `reasoning_depth=1` | 快脑 |
| Worker 错误恢复 | `quality_critical=True, depth=3` | 慢脑 |
| Planner 任务规划 | `quality_critical=True, depth=3` | 慢脑 |
| Planner 回答 Worker 问题 | `latency_critical=True` | 快脑 |
| 复杂度评估 | `latency_critical=True` | 快脑 |

## 工程实现

- `DualBrain`（`src/multi_agent/dual_brain.py`）：适配层，内部创建 `DualBrainRouter`，暴露 `generate(hint=...)` 和 `chat_with_tools(hint=...)` 接口，同时保留 `slow_generate()` / `fast_generate()` 向后兼容。
- `DualBrainRouter`（`src/multi_agent/brain_router.py`）：纯路由逻辑，`pick(hint)` 返回 `(provider, model_override)`。
- AgentLoop 快/慢脑切换（`src/agent/loop.py`）：独立于多 Agent 系统，保持现有逻辑。

## 与安全策略的关系

- 双脑是"算力与质量分配"机制，不替代安全校验。
- 高风险动作仍以 Tool Policy + Release Gate 为准。
- BrainHint 不影响工具授权判定。

## 调优建议

- 将快脑限定在低风险、低副作用任务。
- 为慢脑设置明确预算与重试上限。
- 通过观测指标验证切换策略收益（时延、成功率、成本）。
- 多 Agent 场景中 Worker 正常执行走快脑可显著降低 Token 消耗。
