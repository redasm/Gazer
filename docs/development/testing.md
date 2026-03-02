# 测试与质量门禁

## 测试层次

- **单元测试**：函数与边界行为。
- **集成测试**：Agent + LLM + Tool 主链路。
- **安全回归**：鉴权、策略阻断、敏感数据保护。
- **多 Agent 测试**：BrainHint 路由、复杂度评估、DAG 执行、Worker 协同。

## 推荐执行顺序

1. 运行与改动模块直接相关的测试。
2. 通过后再运行 `pytest -q` 全量回归。

## 最低门禁

- 行为改变必须有测试证据。
- 安全相关改动必须包含回归用例。
- 文档与实现必须同步，不允许"代码已变更、文档未更新"。

## 常用命令

```bash
# 安全回归
pytest -q tests/test_security_regressions.py

# 工具与技能
pytest -q tests/test_tools_and_skills.py

# 多 Agent 系统
pytest -q tests/test_brain_router.py       # BrainHint 路由
pytest -q tests/test_assessor.py           # 复杂度评估器
pytest -q tests/test_worker_agent.py       # Worker 执行与 Hint 路由
pytest -q tests/test_multi_agent_runtime.py # 端到端集成

# 全量回归
pytest -q
```

## 多 Agent 测试要点

- `test_brain_router.py`：验证三维决策矩阵各分支覆盖、BrainHint 不可变性。
- `test_assessor.py`：验证分数到 worker_hint 映射、JSON 解析鲁棒性、LLM 故障降级。
- `test_worker_agent.py`：验证正常执行走快脑、错误恢复切慢脑。
- `test_multi_agent_runtime.py`：端到端验证 Planner-Worker 协同、任务完成与结果汇总。
