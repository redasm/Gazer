"""Agent loop constants.

Extracted from loop.py to avoid circular imports between loop.py and mixin modules.
"""

import re
from typing import Dict

from tools.base import ToolSafetyTier

# Explicit __all__ so that `from agent.constants import *` also exports
# underscore-prefixed names used by mixin modules.
__all__ = [
    "DEFAULT_MAX_ITERATIONS", "HISTORY_CACHE_LIMIT", "MAX_CONTEXT_OVERFLOW_RETRIES",
    "DEFAULT_LLM_MAX_RETRIES", "DEFAULT_LLM_RETRY_BACKOFF_SECONDS",
    "CHARS_PER_TOKEN_ESTIMATE", "FAST_BRAIN_MAX_LENGTH",
    "DEFAULT_TOOL_CALL_TIMEOUT_SECONDS", "DEFAULT_TOOL_RETRY_MAX",
    "DEFAULT_TOOL_RETRY_BACKOFF_SECONDS", "DEFAULT_MAX_TOOL_CALLS_PER_TURN",
    "DEFAULT_MAX_PARALLEL_TOOL_CALLS", "DEFAULT_TOOL_BATCH_MAX_SIZE",
    "DEFAULT_PARALLEL_TOOL_LANE_LIMITS", "DEFAULT_RETRY_BUDGET_TOTAL",
    "DEFAULT_TURN_TIMEOUT_SECONDS",
    "_LANG_DEFAULT", "_CJK_RE",
    "TRUSTED_LOCAL_COMMAND_CHANNELS",
    "_TIER_MAP", "_REPLAN_ERROR_HINTS", "_LANG_MESSAGES",
]

# Agent loop constants (formerly magic numbers)
DEFAULT_MAX_ITERATIONS = 20
HISTORY_CACHE_LIMIT = 50
MAX_CONTEXT_OVERFLOW_RETRIES = 2  # How many times to auto-compact on overflow
DEFAULT_LLM_MAX_RETRIES = 1
DEFAULT_LLM_RETRY_BACKOFF_SECONDS = 0.35
CHARS_PER_TOKEN_ESTIMATE = 4.0
FAST_BRAIN_MAX_LENGTH = 50  # Messages shorter than this may use fast_brain
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 90.0
DEFAULT_TOOL_RETRY_MAX = 1
DEFAULT_TOOL_RETRY_BACKOFF_SECONDS = 0.25
DEFAULT_MAX_TOOL_CALLS_PER_TURN = 12
DEFAULT_MAX_PARALLEL_TOOL_CALLS = 4
DEFAULT_TOOL_BATCH_MAX_SIZE = 4
DEFAULT_PARALLEL_TOOL_LANE_LIMITS: Dict[str, int] = {
    "io": 2,
    "device": 1,
    "network": 2,
    "default": 2,
}
DEFAULT_RETRY_BUDGET_TOTAL = 8
DEFAULT_TURN_TIMEOUT_SECONDS = 180.0
_LANG_DEFAULT = "zh"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
FAST_BRAIN_PATTERNS = {
    "hi", "hello", "hey", "你好", "嗨", "在吗", "在不",
    "thanks", "thank you", "谢谢", "ok", "good", "好的",
    "bye", "再见", "晚安", "早安", "gn", "gm",
}
TRUSTED_LOCAL_COMMAND_CHANNELS = {"web", "gazer"}

_TIER_MAP: Dict[str, ToolSafetyTier] = {
    "safe": ToolSafetyTier.SAFE,
    "standard": ToolSafetyTier.STANDARD,
    "privileged": ToolSafetyTier.PRIVILEGED,
}

_REPLAN_ERROR_HINTS: Dict[str, str] = {
    "TOOL_CIRCUIT_OPEN": "Tool circuit is open. Do not retry the same tool immediately; choose alternative actions.",
    "TOOL_NOT_PERMITTED": "Current tool is blocked by policy. Select a permitted tool or provide a non-tool fallback.",
    "TOOL_PARAMS_INVALID": "Tool arguments were invalid. Rebuild arguments from schema before next call.",
    "TOOL_EXECUTION_FAILED": "Tool execution failed. Avoid repeating identical call and provide a recovery path.",
    "TOOL_TIMEOUT": "Tool call timed out. Reduce scope, increase tool timeout, or choose a faster alternative.",
    "TOOL_ARGS_INVALID": "Tool arguments were invalid. Provide a JSON object matching the tool schema.",
    "WEB_FETCH_FAILED": "Web fetch failed. Validate URL and try a different source if needed.",
    "DEVICE_ACTION_UNSUPPORTED": "Requested device action is unsupported on current node. Use supported actions only.",
}

_LANG_MESSAGES: Dict[str, Dict[str, str]] = {
    "zh": {
        "iteration_limit_fallback": "我在迭代次数上限内未能完成本次请求。请重试或简化需求。",
        "iteration_finalize_system": (
            "已达到迭代上限。请立即给出最终回复："
            "1) 概述已完成进度；2) 说明当前阻塞原因；3) 给出下一步可执行建议。"
            "不要调用任何工具。"
        ),
        "timeout": "抱歉，本次请求在 {seconds}s 后超时，请重试。",
        "runtime_error": "抱歉，我处理请求时遇到错误：{error}",
        "llm_error": "抱歉，我暂时无法得到有效模型回复。详情：{detail}",
        "fake_tool_retry": (
            "错误：你声称已执行操作，但没有调用任何工具。"
            "你必须实际调用对应工具（例如 `screenshot`）。"
            "请重试并真实调用工具。"
        ),
        "tool_call_limit": (
            "本轮工具调用超出上限（limit={limit}, executed={executed}, requested={requested}）。"
            "请缩小任务范围或分步执行。"
        ),
        "language_rule": "语言规则：回复跟随用户输入语言；若无法判断，默认使用中文。",
    },
    "en": {
        "iteration_limit_fallback": (
            "I wasn't able to complete my response within the iteration limit. "
            "Please try again or simplify your request."
        ),
        "iteration_finalize_system": (
            "Iteration limit reached. Provide a final concise response now. "
            "Summarize completed progress, explain what blocked completion, and suggest "
            "the next actionable step. Do not call tools."
        ),
        "timeout": "Sorry, this request timed out after {seconds}s. Please try again.",
        "runtime_error": "Sorry, I encountered an error: {error}",
        "llm_error": "Sorry, I couldn't get a valid model response right now. Details: {detail}",
        "fake_tool_retry": (
            "ERROR: You claimed to perform an action but did NOT call any tool. "
            "You MUST actually call the tool (e.g. `screenshot`) to perform the action. "
            "Please try again and USE THE TOOL."
        ),
        "tool_call_limit": (
            "Tool-call limit exceeded for this turn "
            "(limit={limit}, executed={executed}, requested={requested}). "
            "Reduce scope or split the task into smaller steps."
        ),
        "language_rule": "Language policy: reply in the user's language; default to Chinese if unclear.",
    },
}
