# 工具策略参考（Tool Policy）

策略核心位于 `src/tools/registry.py`。

## 策略维度

- allowlist / denylist
- tool group
- safety tier
- provider 限制
- 渠道/用户上下文限制（通过运行时注入）

## 评估流程

1. 归一化策略（normalize）
2. 基于工具元数据评估可见性与可执行性
3. 返回 allow/deny 与原因
4. 执行前再次检查（防并发时序绕过）

## 安全等级建议

- `low`：只读、低影响能力
- `medium`：可写但可回滚能力
- `high`：系统命令、设备动作、外部副作用强能力

## 与 Release Gate 的关系

- Gate 是高阶治理策略，不替代工具级权限控制。
- 当 Gate 被阻断，高风险 tier 必须硬阻断。

## MCP 策略联动

- `/mcp` 的 tools/resources/prompts 访问受 `api.mcp.*` 控制。
- 建议在开放外部 MCP 客户端前，先通过 `POST /mcp/policy/simulate` 验证放行路径。
- 审计入口：`GET /mcp/audit`，可检索方法、耗时、错误码与目标（tool/resource/prompt）。

## 调试建议

- 使用策略模拟接口先验证放行/拒绝路径。
- 审计日志需记录“谁在何时为何被拒绝”。
