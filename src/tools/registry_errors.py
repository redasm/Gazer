from __future__ import annotations

import time
import uuid

DEFAULT_TOOL_ERROR_HINTS: dict[str, str] = {
    "TOOL_NOT_FOUND": "Tool 名称不存在。先调用 tool definitions 获取可用工具列表，再重试。",
    "TOOL_NOT_PERMITTED": "工具被安全策略拦截。检查 owner 权限 / allowlist 配置。",
    "TOOL_PARAMS_INVALID": "参数不符合工具 schema。请根据工具 parameters 重新构建参数对象。",
    "TOOL_CIRCUIT_OPEN": "该工具近期连续失败触发熔断。等待冷却或改用替代工具路径。",
    "TOOL_BUDGET_EXCEEDED": "工具调用预算超限。降低调用频率或调整 security.tool_budget_* 配置。",
    "TOOL_CANCELLED": "操作已取消。必要时重新发起请求。",
    "TOOL_BLOCKED_BY_HOOK": "被插件 Hook 拦截。检查 plugins/hook 配置与日志。",
    "TOOL_EXECUTION_FAILED": "执行失败。检查依赖、权限、网络、以及工具日志；避免重复相同调用。",
}


def format_tool_error(code: str, message: str, *, trace_id: str = "", hint: str = "") -> str:
    """Render the standard human-readable tool error payload."""
    head = f"Error [{code}]: {message}"
    if trace_id:
        head = f"{head} (trace_id={trace_id})"
    resolved_hint = str(hint or DEFAULT_TOOL_ERROR_HINTS.get(code, "")).strip()
    if not resolved_hint:
        return head
    return f"{head}\nHint: {resolved_hint}"


def new_tool_trace_id() -> str:
    return f"trc_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
