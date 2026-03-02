# 配置参考（Config）

配置系统核心位于 `src/runtime/config_manager.py`。

## 核心能力

- 读取：`get(key_path, default)`
- 更新：`set(key_path, value)` / `set_many(updates)`
- 持久化：`save()`
- 安全输出：`to_safe_dict()`（敏感项掩码）

## 设计约束

- 使用点路径（dot-path）访问配置键。
- 敏感字段通过匹配规则自动掩码。
- 默认配置与用户配置合并时保留敏感默认值保护。

## 推荐配置分组

- `llm.*`：模型、路由、预算
- `tools.*`：工具策略与安全级别
- `memory.*`：存储、索引、召回参数
- `soul.*`：人格、认知流程
- `channels.*`：通道接入参数
- `security.*`：鉴权、限流、门禁策略
- `observability.*`：可观测与门禁联动阈值
- `plugins.*`：插件启停与签名校验策略
- `trainer.*`：训练样本、实验、发布策略
- `discord.*`：Discord 渠道配置
- `multi_agent.*`：多 Agent 协同配置
- `runtime.*` / `satellite.*`：Python/Rust backend 切换与 sidecar 参数

## 关键示例：多 Agent 协同

```yaml
multi_agent:
  allow_multi: false   # 是否允许系统自动路由到多 Agent
  max_workers: 5       # Worker 数量上限（系统按评估分数动态决定实际数量）
```

- `allow_multi=true` 时，`process_auto()` 会用快脑评估任务复杂度，分数 ≥ 2 自动走多 Agent 路径。
- `max_workers` 是 AgentPool 的硬上限，实际 Worker 数由 `TaskComplexityAssessor` 的 `worker_hint` 决定。
- Web 管理台路径：Settings → Smart Collaboration。

## 关键示例：门禁健康联动阈值

```yaml
observability:
  release_gate_health_thresholds:
    warning_success_rate: 0.9
    critical_success_rate: 0.75
    warning_failures: 1
    critical_failures: 3
    warning_p95_latency_ms: 2500
    critical_p95_latency_ms: 4000
    warning_persona_consistency_score: 0.82
    critical_persona_consistency_score: 0.70
```

用于 `Release Gate` 页和 `/debug/release-gate` 的"门禁 × 工作流健康"评估。

## 关键示例：插件签名与完整性策略

```yaml
plugins:
  enabled: []
  disabled: []
  signature:
    enforce: false
    allow_unsigned: true
    trusted_keys:
      dev-key: "your-hmac-secret"
```

- `enforce=true` 且 `allow_unsigned=false` 时，未签名插件会被拒绝加载。
- 清单中的 `integrity` 字段用于文件 SHA256 完整性校验。

## 关键示例：训练与发布策略

```yaml
trainer:
  enabled: true
  auto_run_on_gate_fail: true
  max_samples_per_job: 200
  auto_publish_on_pass: false
  canary:
    default_percent: 10
    auto_rollback_on_gate_fail: true
  experiments:
    enabled: true
```

- 用于 Sample Store、Experiment、Canary 发布与自动回滚。

## 关键示例：Discord 渠道

```yaml
discord:
  enabled: false
  token: "${DISCORD_BOT_TOKEN}"
  allowed_guild_ids: []
  dm_policy: pairing
```

## 关键示例：Rust Sidecar 执行后端

```yaml
runtime:
  backend: python   # python | rust
  rust_sidecar:
    endpoint: "http://127.0.0.1:8787"
    timeout_ms: 3000
    auto_fallback_on_error: true
    error_fallback_threshold: 3
    rollout:
      enabled: false
      owner_only: false
      channels: []   # e.g. ["feishu", "web"]

coding:
  exec_backend: local   # local | sandbox | ssh | rust
  max_output_chars: 100000
  max_parallel_tool_calls: 4
  allow_local_fallback: false

devices:
  local:
    backend: python     # python | rust

satellite:
  transport_backend: python   # python | rust
  max_pending_requests_per_node: 64
  pending_ttl_seconds: 30.0
  heartbeat_timeout_seconds: 45.0
  frame_window_seconds: 2.0
  max_frame_bytes_per_window: 4194304
```

## 关键示例：OpenViking 记忆后端预检

```yaml
memory:
  context_backend:
    enabled: false
    mode: openviking
    data_dir: data/openviking
    config_file: ""
    session_prefix: gazer
    default_user: owner
    commit_every_messages: 8
```

- `enabled=true` 时，启动前会检查 `openviking` 依赖和配置文件有效性。
- `config_file` 非空时必须存在，否则启动失败。

## 变更建议

- 每次新增配置项都要补：
  - 默认值
  - 风险说明
  - 配套测试
  - 文档更新
