# 管理 API 参考（Admin API）

管理 API 主要定义在 `src/tools/admin_api.py`。

## 鉴权与会话

- `POST /auth/session`
- `DELETE /auth/session`

除公开端点外，大多数接口依赖管理员 token 验证。

## 配置与策略

- `GET /config`
- `POST /config`
- `POST /mcp`（MCP JSON-RPC：`initialize` / `tools/*` / `resources/*` / `prompts/*`）
- `GET /mcp/policy`
- `POST /mcp/policy/simulate`
- `GET /mcp/audit`
- `DELETE /mcp/audit`
- `POST /policy/explain`
- `POST /policy/simulate`
- `GET /policy/effective`
- `GET /policy/audit`

## LLM 治理与观测

- `GET /llm/router/status`
- `POST /llm/router/strategy`
- `GET /llm/router/budget`
- `POST /llm/router/budget`
- `GET /llm/deployment-profiles`
- `POST /llm/deployment-profiles`
- `GET /llm/deployment-targets`
- `POST /llm/deployment-targets`
- `PUT /llm/deployment-targets/{target_id}`
- `DELETE /llm/deployment-targets/{target_id}`
- `GET /llm/deployment-targets/status`
- `GET /llm/deployment-targets/health`
- `GET /observability/metrics`（包含 `provider/model/agent/workflow` 四层汇总）

## 记忆、技能、任务

- `GET /memory/recent`
- `GET /memory/search`
- `GET /skills`
- `POST /skills`
- `GET /cron`
- `POST /cron`
- `GET /workflows/graphs`
- `GET /workflows/graphs/{workflow_id}`
- `POST /workflows/graphs`
- `DELETE /workflows/graphs/{workflow_id}`
- `POST /workflows/graphs/{workflow_id}/run`
- `POST /workflows/flowise/import`（Flowise -> Gazer，返回节点级错误）
- `POST /workflows/flowise/export`（Gazer -> Flowise-compatible JSON）

Flowise 映射规则与不支持清单见：`docs/reference/flowise-interop.md`。

## 调试与评测

- `GET /debug/trajectories`
- `GET /debug/eval-benchmarks`
- `POST /debug/eval-benchmarks/{dataset_id}/run`
- `GET /debug/release-gate`
- `POST /debug/release-gate/override`
- `GET /debug/training-jobs`
- `POST /debug/training-jobs`
- `GET /debug/training-jobs/{job_id}`
- `POST /debug/training-jobs/{job_id}/run`
- `POST /debug/training-jobs/{job_id}/publish`
- `GET /debug/training-bridge/exports`
- `POST /debug/training-bridge/exports`
- `GET /debug/training-bridge/exports/{export_id}`
- `GET /debug/training-bridge/exports/{export_id}/compare`
- `GET /debug/training-bridge/compare/latest`
- `GET /debug/training-bridge/exports/{export_id}/training-inputs`
- `POST /debug/training-bridge/exports/{export_id}/to-sample-store`
- `GET /debug/training-sample-stores`
- `GET /debug/training-sample-stores/{store_id}`
- `POST /debug/training-sample-stores/from-benchmark`
- `GET /debug/training-experiments`
- `GET /debug/training-experiments/{experiment_id}`
- `POST /debug/training-experiments`
- `POST /debug/training-experiments/{experiment_id}/run`
- `GET /debug/training-experiments/{experiment_id}/compare`
- `GET /debug/training-releases`
- `GET /debug/training-releases/{release_id}`
- `POST /debug/training-releases/{release_id}/promote`
- `POST /debug/training-releases/{release_id}/rollback`
- `POST /debug/persona-eval/datasets/{dataset_id}/run`
- `GET /debug/persona-eval/datasets/{dataset_id}/runs`
- `GET /debug/persona-eval/datasets/{dataset_id}/latest`
- `GET /debug/persona/mental-process`
- `POST /debug/persona/mental-process`
- `GET /debug/persona/mental-process/versions`
- `GET /debug/persona/mental-process/versions/{version_id}`
- `POST /debug/persona/mental-process/rollback`
- `GET /debug/persona/runtime-signals`
- `GET /debug/persona/runtime-signals/latest`
- `POST /debug/persona/runtime-correction/simulate`

## 插件市场（最小闭环）

- `GET /plugins/market`：插件列表（包含签名/完整性校验状态）
- `GET /plugins/market/{plugin_id}`：插件详情
- `POST /plugins/market/install`：安装插件（本地目录源）
- `POST /plugins/market/toggle`：启用/禁用插件

## 部署治理与可观测增强

- `GET /llm/deployment-profiles`
- `POST /llm/deployment-profiles`
- `GET /llm/deployment-targets`
- `POST /llm/deployment-targets`
- `GET /llm/deployment-targets/status`
- `GET /llm/deployment-targets/health`
- `GET /llm/router/budget`
- `POST /llm/router/budget`
- `GET /observability/metrics`（包含 `tool_governance`）
- `GET /observability/trends`
- `GET /observability/alerts`
- `DELETE /observability/alerts`

## WebSocket 端点

- `/ws/status`
- `/ws/chat`
- `/ws/canvas`

> 说明：本页是能力索引，后续可继续拆成”请求体/响应体/错误码”详细表。
