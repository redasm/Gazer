# 安全模型（Security）

Gazer 的安全控制采用“身份鉴别 -> 策略授权 -> 执行防护 -> 审计观测”的分层模型。

## 安全边界

- 外部输入边界：HTTP、WebSocket、Channel 消息均视为不可信输入。
- 工具执行边界：高风险工具必须经过 tier/policy/gate 多重判定。
- 进程通信边界：Brain 与 Admin API 间 IPC 消息需签名验证。
- 配置与身份边界：Owner/Admin Token/Session 全部集中管理。

## 关键代码

- Owner 与会话：`src/security/owner.py`
- 配对审批：`src/security/pairing.py`
- 威胁扫描：`src/security/threat_scan.py`
- Admin 鉴权：`src/tools/admin/auth.py`
- IPC 签名：`src/runtime/ipc_secure.py`
- 日志脱敏：`src/runtime/log_sanitizer.py`
- 工具策略与预算：`src/tools/registry.py`

## 认证与授权链路

1. 管理端请求进入 FastAPI 路由。
2. `verify_admin_token` 校验：
   - Bearer Token 或 HttpOnly Cookie Session
   - Origin/CORS 规则
   - 本地开发环境有限放行策略
3. 通过后才允许访问受保护 API。
4. 工具执行阶段再由 `ToolRegistry` 做二次授权（tier、allow/deny、provider、预算、熔断）。

## 配对与 DM 策略

- Channel 层由 `ChannelAdapter` 执行 `dm_policy`：`open` / `allowlist` / `pairing`。
- 在 `pairing` 模式下，未知 sender 会收到配对码。
- 管理员通过 `/pairing/*` 接口审批后才允许继续会话。
- 持久化文件默认位于 `data/openviking/pairing.json`。

## IPC 与日志防护

- `SecureQueue` 对队列消息封装 HMAC-SHA256 签名。
- 消息含时间戳并校验最大存活时间，降低重放风险。
- 全局日志过滤器脱敏 key/token/password/authorization 等字段。

## 配置重点

- `security.dm_policy`
- `security.owner_channel_ids`
- `security.tool_max_tier`
- `security.tool_denylist` / `security.tool_allowlist`
- `security.tool_budget_*`
- `api.cors_*` 与 `api.require_origin_for_mutations`

配置文件：`config/settings.yaml`

## 测试建议

- `pytest -q tests/test_security_regressions.py`
- `pytest -q tests/test_tools_and_skills.py`

若改动身份、配对、工具策略或鉴权流程，建议至少补一条回归测试再合并。
