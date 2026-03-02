# Tools 与策略治理

工具系统由 `ToolRegistry` 统一托管，策略层决定“什么能调用、由谁调用、在何时调用”。

## 关键代码

- 工具注册与执行：`src/tools/registry.py`
- 工具基类与安全等级：`src/tools/base.py`
- 策略管线：`src/agent/tool_policy_pipeline.py`
- 人格信号联动：`src/agent/persona_tool_policy.py`
- 管理 API：`src/tools/admin/policy.py`

## ToolRegistry 核心能力

- 动态注册工具与 provider 分组。
- 安全分级（`ToolSafetyTier`）过滤。
- allowlist/denylist 与 provider/model 维度策略匹配。
- 工具熔断（连续失败触发 cooldown）。
- 调用预算（全局、group、tool 权重）与拒绝事件审计。

## 策略叠加顺序

常见执行顺序（最终策略按收敛规则合成）：

1. 全局策略（`security.*`）
2. 目录/agents overlay 策略
3. Agent 级策略
4. 请求级临时策略
5. 人格运行时联动策略（warning/critical）

规则：

- Allow 集合是“交集收缩”
- Deny 集合是“并集扩张”
- Deny 优先于 Allow

## Admin API 相关接口

- `POST /policy/explain`：解释单个工具为何放行/拒绝
- `POST /policy/simulate`：批量模拟策略结果
- `GET /policy/effective`：查看当前生效策略
- `GET/POST /debug/agents-md/effective`
- `POST /debug/agents-md/lint`

## 典型错误码

- `TOOL_NOT_FOUND`
- `TOOL_NOT_PERMITTED`
- `TOOL_CIRCUIT_OPEN`
- `TOOL_BUDGET_EXCEEDED`
- `TOOL_EXECUTION_FAILED`

这些错误会带规范化提示，便于上层 Agent 进行重试或改道。

## 变更建议

涉及工具权限改动时，建议同时验证：

- `tests/test_tools_and_skills.py`
- `tests/test_security_regressions.py`

并通过 `/policy/simulate` 做线上前置演练。
