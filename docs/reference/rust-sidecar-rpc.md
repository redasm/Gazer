# Rust Sidecar RPC（Phase 0）

适用代码路径：

- `src/runtime/rust_rpc.py`
- `src/runtime/rust_sidecar.py`
- `src/runtime/brain.py`

## 协议目标

- Python 控制平面与 Rust 执行平面通过独立 sidecar 进程通信。
- Phase 0 固定最小健康探测端点：`/health`、`/version`、`/capabilities`。
- RPC 统一封装 `trace_id`，并将 sidecar 错误映射到 Python 现有错误语义。

## Request Schema

`POST /rpc`

```json
{
  "protocol": "gazer-rpc.v1",
  "trace_id": "trc_xxx",
  "method": "shell.exec",
  "params": {
    "command": "pytest -q",
    "cwd": ".",
    "timeout": 30
  }
}
```

字段说明：

- `protocol`：当前固定 `gazer-rpc.v1`
- `trace_id`：链路追踪 ID；未提供时 Python 端自动生成
- `method`：RPC 方法名（如 `shell.exec`、`files.read`）
- `params`：方法参数对象

## Response Schema

成功：

```json
{
  "ok": true,
  "trace_id": "trc_xxx",
  "result": {
    "exit_code": 0,
    "stdout": "ok",
    "stderr": ""
  }
}
```

失败：

```json
{
  "ok": false,
  "trace_id": "trc_xxx",
  "error": {
    "code": "TIMEOUT",
    "message": "deadline exceeded",
    "details": {}
  }
}
```

## 错误码映射

sidecar -> Python（节选）：

- `BAD_REQUEST` / `INVALID_ARGUMENT` -> `TOOL_ARGS_INVALID`
- `PERMISSION_DENIED` -> `TOOL_PERMISSION_DENIED`
- `NOT_FOUND` -> `TOOL_NOT_FOUND`
- `NOT_SUPPORTED` -> `DEVICE_ACTION_UNSUPPORTED`
- `TIMEOUT` / `DEADLINE_EXCEEDED` -> `TOOL_TIMEOUT`
- `UNAVAILABLE` / `CONNECTION_FAILED` -> `RUST_SIDECAR_UNAVAILABLE`
- `INTERNAL` -> `RUST_SIDECAR_INTERNAL`
- 其他 -> `RUST_SIDECAR_ERROR`

## 配置开关

`config/settings.yaml` 与 `src/runtime/config_manager.py` 同步键：

- `runtime.backend: python|rust`
- `runtime.rust_sidecar.endpoint`
- `runtime.rust_sidecar.timeout_ms`
- `runtime.rust_sidecar.auto_fallback_on_error`
- `runtime.rust_sidecar.error_fallback_threshold`
- `runtime.rust_sidecar.rollout.enabled`
- `runtime.rust_sidecar.rollout.owner_only`
- `runtime.rust_sidecar.rollout.channels`
- `coding.exec_backend: local|sandbox|ssh|rust`
- `devices.local.backend: python|rust`
- `satellite.transport_backend: python|rust`

## 当前落地范围

- `coding.exec_backend=rust` 时，启动阶段会先探测 sidecar 健康端点，再注入 `ShellOperations/FileOperations` 的 Rust 适配实现。
- `devices.local.backend` 与 `satellite.transport_backend` 已接入配置与观测字段，用于后续 Phase 1/3 的执行下沉。
- `devices.local.backend=rust` 时，`LocalDesktopNode` 的以下动作走 sidecar RPC：
  - `screen.screenshot` -> `desktop.screen.screenshot`
  - `input.mouse.click` -> `desktop.input.mouse.click`
  - `input.keyboard.type` -> `desktop.input.keyboard.type`
  - `input.keyboard.hotkey` -> `desktop.input.keyboard.hotkey`

## Phase 2 适配（Sandbox/SSH）

- `runtime.backend=rust` + `coding.exec_backend=sandbox`：
  - `sandbox.exec`
  - `sandbox.files.read/write/exists/dir_exists`
- `runtime.backend=rust` + `coding.exec_backend=ssh`：
  - `ssh.exec`
  - `ssh.files.read/write/exists/dir_exists`

## 资源限制（Python 侧）

- `coding.max_output_chars`：远端执行返回的 `stdout/stderr` 截断上限
- `coding.max_parallel_tool_calls`：远端执行并发上限（信号量）

上述限制在 Python backend 与 Rust backend 适配层都生效，保证输出体积与并发可控。

## Phase 3 传输治理（Satellite）

- Session manager 支持：
  - invoke request/response 多路复用（request_id -> pending future）
  - pending TTL 清理与断连回收
  - heartbeat 超时剔除离线会话
- WebSocket `/ws/satellite` 增加大帧背压预算：
  - `satellite.frame_window_seconds`
  - `satellite.max_frame_bytes_per_window`
- 新增状态接口：
  - `GET /satellite/session/status`
  - `manager.last_observation` 至少包含 `trace_id` / `latency_ms` / `error_code`

Rust backend 兼容路径：

- `satellite.transport_backend=rust` 时使用 `RustSatelliteSessionManager`；
- 优先尝试 `satellite.invoke` sidecar RPC，不可用时回退 Python 会话路径。

自动回退策略（执行后端）：

- Rust shell/file 适配层记录 sidecar 连续错误计数；
- 达到 `runtime.rust_sidecar.error_fallback_threshold` 后，
  若启用 `runtime.rust_sidecar.auto_fallback_on_error` 且允许本地回退，
  自动切到 Python 本地执行后端。

灰度发布策略（owner/channel）：

- 当 `runtime.rust_sidecar.rollout.enabled=false` 时，不限制 Rust backend。
- 当 `runtime.rust_sidecar.rollout.enabled=true` 时，仅以下请求可走 Rust：
  - owner 身份请求（按 `security.owner_channel_ids` 判定）；
  - `channel` 在 `runtime.rust_sidecar.rollout.channels` 白名单中。
- 未命中灰度条件的请求自动回退到 Python backend，不影响上层调用语义。

## 风险闭环（本轮）

- 平台差异（输入/截图权限）：
  - 本地节点能力通过 `NodeInfo.metadata` 暴露 `capture/screenshot` 可用性与原因；
  - 不可用时返回显式错误码（如 `DEVICE_SCREENSHOT_UNAVAILABLE`）。
- sidecar 崩溃后的恢复一致性：
  - sidecar 调用连续错误触发自动 fallback（阈值可配）；
  - 卫星会话层保留 Python 路径作为兼容回退。
- Docker/SSH 环境偏移：
  - 统一输出截断、超时、并发上限；
  - Rust 通路受灰度门控，未命中条件自动落回 Python backend。
- 观测与审计字段丢失：
  - 卫星状态保留 `trace_id/latency_ms/error_code`；
  - 管理端可通过 `/satellite/session/status` 与策略审计接口核验。
