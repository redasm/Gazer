# LLM 路由与降级

LLM 层通过 `RouterProvider` 在多模型/多供应商间做动态选择，兼顾成功率、时延与成本。

## 关键代码

- 路由核心：`src/llm/router.py`
- 供应商实现：`src/llm/litellm_provider.py`
- Provider 注册：`src/runtime/provider_registry.py`
- 部署编排：`src/runtime/deployment_orchestrator.py`
- Agent 接入：`src/agent/adapter.py`

## RouterProvider 核心能力

- 路由策略：`priority` / `latency` / `success_rate`
- 策略模板：`cost_first` / `latency_first` / `availability_first`
- 预算控制：calls/cost 窗口限制与自动降级阈值
- 异常剔除：连续失败触发临时 eject + cooldown
- 复杂度路由：基于输入复杂度切换偏好策略

## 路由对象

每条候选路由由 `ProviderRoute` 描述：

- provider 实例与默认 model
- capacity/cost/latency 目标
- 实时健康统计（success rate、p95 latency、连续失败数）

## 典型流程

1. Agent 组装消息并请求 LLM。
2. Router 根据策略与当前健康状态选择 route。
3. 执行请求并回写 route 统计。
4. 若触发预算/异常策略，自动换路或降级。

## 关键配置

- `models.router.enabled`
- `models.router.strategy`
- `models.router.strategy_template`
- `models.router.budget.*`
- `models.router.outlier_ejection.*`
- `models.router.complexity_routing.*`

配置文件：`config/settings.yaml`

## 运维建议

- 通过观测接口持续看各 route 的成功率与 p95 延迟。
- 成本敏感场景优先启用 `cost_first` + budget。
- 高可用场景优先 `availability_first`，同时设置合理 cooldown。
