# 卫星模式（Satellite Mode）

卫星模式用于将单机 Gazer 扩展为多节点协同执行，核心是“受控接入 + 实时会话 + 远程动作回传”。

## 模块职责

- 维护卫星 WebSocket 会话与心跳。
- 接收远程屏幕帧并注入感知源。
- 下发动作请求并等待 `invoke_result`。
- 提供配对审批 API（pending/approve/reject/revoke）。

## 关键代码

- 管理路由：`src/tools/admin/satellite.py`
- 会话管理：`src/devices/satellite_session.py`
- 协议定义：`src/devices/satellite_protocol.py`
- 远程节点适配：`src/devices/adapters/remote_satellite.py`
- Brain 装配入口：`src/runtime/brain.py`（`_init_capture`、`_init_devices`）

## 协议与会话流程

1. 卫星连接 `/ws/satellite`。
2. 首帧必须发送 `hello`（含 `node_id` 与 token）。
3. 服务端校验通过后注册会话并返回 `ack`。
4. 卫星持续发送：
   - `heartbeat`（更新会话活性）
   - `frame`（图像帧）
   - `invoke_result`（远程动作执行结果）
5. 主站动作调用时，`SatelliteSessionManager.send_invoke()` 下发 `InvokeRequest` 并等待回包。
6. 超时、断连或心跳失效会触发下线与 pending 请求清理。

## 管理端接口

- `POST /satellite/snapshot`（兼容旧式上传）
- `GET /satellite/view`
- `GET /satellite/session/status`
- `WS  /ws/satellite`
- `GET /pairing/pending`
- `GET /pairing/approved`
- `POST /pairing/approve`
- `POST /pairing/reject`
- `POST /pairing/revoke`

## 可靠性与回压

- 单帧大小限制：超限直接断开。
- 窗口预算控制：超过 `max_frame_bytes_per_window` 触发 backpressure 断连。
- pending 请求数限制：每节点 `max_pending_requests_per_node`。
- 心跳超时剔除：`heartbeat_timeout_seconds`。

## 典型排障

- 一直 `hello required`：卫星未先发 `hello`。
- `Satellite auth failed`：节点 token 或节点配置不匹配。
- `DEVICE_TARGET_OFFLINE`：会话未注册或已心跳超时。
- `DEVICE_INVOKE_TIMEOUT`：卫星端未及时返回 `invoke_result`。
