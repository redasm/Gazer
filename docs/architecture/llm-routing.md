# LLM 路由与部署治理

LLM 层负责供应商接入、路由策略、预算约束与可观测性。

## 目标

- 在质量、时延、成本之间做动态平衡。
- 支持 provider 失败时的故障切换与降级。
- 支持开发态实验与生产态稳态策略并存。

## 关键能力（当前项目）

- 模型路由状态查询：`/llm/router/status`
- 路由策略调整：`/llm/router/strategy`
- 预算配置读写：`/llm/router/budget`
- 部署画像管理：`/llm/deployment-profiles`
- 部署目标管理：`/llm/deployment-targets*`
- 部署目标状态与探测：`/llm/deployment-targets/status`、`/llm/deployment-targets/health`

以上接口位于 `src/tools/admin_api.py`（管理员鉴权后可用）。

## 推荐路由策略

1. 快速脑（低成本、低延迟）优先处理轻量请求。
2. 深度脑（高质量）处理复杂计划、关键决策与高风险任务。
3. 当预算达到阈值时自动降级模型层级。

## 可观测建议

- 至少记录三层指标：provider/model/agent。
- 关键指标：成功率、P95、错误类型、预算消耗。
- 对比分析应支持“策略变更前后”的同口径对照。

## 部署目标与摘除机制

- Router 支持两类路由输入：
  - `models.router.candidates`：直接按 provider 名称路由（兼容旧配置）。
  - `models.router.deployment_targets`：按部署目标 ID 路由（推荐生产使用）。
- 每个部署目标可覆盖 `base_url`、`api_key`、`default_model`，并可绑定 `models.deployment_profiles` 的容量/成本/时延画像。
- Outlier ejection（异常节点摘除）由 `models.router.outlier_ejection` 控制：
  - `enabled`
  - `failure_threshold`
  - `cooldown_seconds`
- 被摘除目标会在冷却期内自动跳过，冷却后自动恢复候选。
