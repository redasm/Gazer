# MCP 集成参考

Gazer 管理端提供 `POST /mcp` JSON-RPC 入口，用于标准化工具、资源与提示词能力对接。

## 端点

- `POST /mcp`
- `POST /mcp/`

## 已支持方法

- `initialize`
- `ping`
- `notifications/initialized`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `prompts/list`
- `prompts/get`

## 资源 URI

- `gazer://config/safe`
- `gazer://llm/router/status`
- `gazer://memory/recent?limit=20`
- `gazer://eval/benchmark/latest?dataset_id=<id>&include_compare=true&baseline_index=1&include_workflow=true`
- `gazer://eval/gate/status?include_streak=true&include_resolved_tasks=true&streak_limit=10&dataset_id=<id>&include_workflow=true`
- `gazer://eval/trainer/latest?status=completed&include_output=false&include_workflow=true`

## 常见用途

- 通过 `tools/list` 自动发现可调用工具。
- 通过 `resources/read` 获取可观测状态快照。
- 通过 `prompts/get` 拉取标准提示模板用于外部编排。
- 通过 `gazer://eval/*` 把评测、门禁、训练状态接入 CI / 发布守门自动化。

## 参数说明（评测资源）

- `dataset_id`：指定 benchmark 数据集，不传则取最近一个数据集。
- `include_compare`：`true/false`，是否返回与历史基线对比。
- `baseline_index`：基线索引（默认 `1`，表示“与上一次运行比”）。
- `include_output`：`true/false`，仅用于 `eval/trainer/latest`；默认 `false`（返回 `output_summary`），为 `true` 时返回完整 `output`。
- `include_streak`：`true/false`，仅用于 `eval/gate/status`；为 `true` 时返回 fail streak 与最近优化任务摘要。
- `streak_limit`：`eval/gate/status` 的 streak/task 返回数量上限（默认 `10`，最大 `50`）。
- `include_resolved_tasks`：`true/false`，仅用于 `eval/gate/status`；为 `true` 时额外返回最近 `resolved` 状态优化任务摘要。
- `include_workflow`：`true/false`，用于 `gazer://eval/*`；默认 `true`，返回 `workflow_observability`（运行成功率、P95、错误分类、按工作流聚合）。
- `workflow_limit`：工作流观测采样上限（默认 `100`，最大 `1000`）。

## 鉴权

`/mcp` 受管理端鉴权保护，调用时需携带有效管理员认证信息。

## 治理与审计

- 运行时策略读取：`GET /mcp/policy`
- 策略模拟（变更前验证）：`POST /mcp/policy/simulate`
- MCP 审计查询：`GET /mcp/audit`
- MCP 审计清理：`DELETE /mcp/audit`

`api.mcp` 配置项（`config/settings.yaml`）：

- `enabled`
- `rate_limit_requests`
- `rate_limit_window_seconds`
- `allow_tools`
- `allow_resources`
- `allow_prompts`
- `allowed_resource_prefixes`
- `allowed_prompt_names`
- `audit_retain`

## 错误码映射

- `-32600`：Invalid Request（请求结构错误）
- `-32601`：Method not found（方法不存在）
- `-32602`：Invalid params（参数错误）
- `-32000`：Dependency unavailable（如 ToolRegistry 不可用）
- `-32002`：Unsupported/Not found resource（资源协议不支持或不存在）
- `-32003`：Prompt not found（提示模板不存在）
- `-32010`：Access denied by MCP policy（被 MCP 策略拒绝）
- `-32029`：Rate limit exceeded（MCP 速率超限）
- `-32030`：MCP endpoint disabled（MCP 被策略关闭）
