# Web 管理台（Web Console）

Web 管理台是运行与运维入口，负责配置管理、实时聊天、策略治理、观测调试与发布控制。

## 前后端结构

- 前端：`web/`（React + Vite）
- 后端：`src/tools/admin_api.py`（FastAPI 宿主）
- 路由模块：`src/tools/admin/*.py`（按域拆分）

`src/tools/admin/__init__.py` 中维护统一路由注册表（auth/git/cron/skills/websockets/policy/memory/observability/satellite 等）。

## 前端关键页面

- 主入口：`web/src/main.jsx`、`web/src/App.jsx`
- 页面目录：`web/src/pages/*`
- 代表页面：
  - `Dashboard.jsx`
  - `Chat.jsx`
  - `Settings.jsx`
  - `ToolPolicy.jsx`
  - `LlmRouter.jsx`
  - `Observability.jsx`
  - `Security.jsx`
  - `MemoryGalaxy.jsx`

API 基地址由 `web/src/config.js` 提供，默认 `http://localhost:8080`。

## 后端关键能力

- 会话鉴权：`/auth/session`
- 配置读写：`/config/*`
- 聊天与流式事件：`/ws/chat`
- 状态广播：`/ws/status`
- Canvas/A2UI：`/ws/canvas`
- 策略治理：`/policy/*`
- 观测与日志：`/observability/*`、`/logs/*`
- 卫星节点：`/ws/satellite`、`/satellite/*`、`/pairing/*`

## 运行模式

- 开发模式：
  - 后端：`python main.py`
  - 前端：`cd web && npm run dev`
- 生产模式：
  - `web/dist` 存在时，Admin API 会托管静态资源并启用 SPA fallback。

## 安全边界

- WebSocket 连接同样走 `_verify_ws_auth`。
- 管理接口默认需要 Admin Token 或有效 Session。
- 变更类请求建议保留 Origin 校验（`api.require_origin_for_mutations=true`）。

## 排障提示

- 前端无法连后端：检查 `VITE_API_BASE` 与 `ADMIN_API_PORT`。
- 已登录但接口 401/403：检查 Cookie `SameSite/Secure` 与 CORS。
- WebSocket 无消息：确认 API 进程 output queue 与 `_broadcast_output_worker` 正常。
