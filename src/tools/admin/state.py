"""Runtime globals and in-memory buffers for the Admin API layer.

Convention:
    * UPPER_CASE names are *injected at runtime* by ``brain.py``.
      They start as ``None`` and are set once during startup.
    * ``_lowercase`` buffers are shared circular buffers used across routers.
    * ``get_xxx()`` accessor functions read from :class:`AppContext`.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from runtime.app_context import get_app_context
from runtime.config_manager import config
from runtime.paths import resolve_runtime_path


def get_max_ws_message_bytes() -> int:
    """Return the WebSocket message byte limit (reads config dynamically)."""
    return int(config.get("api.max_ws_message_bytes", 256 * 1024))


def get_max_chat_message_chars() -> int:
    """Return the chat message character limit (reads config dynamically)."""
    return int(config.get("api.max_chat_message_chars", 8000))


if TYPE_CHECKING:
    from tools.canvas import CanvasState
    from scheduler.cron import CronScheduler

logger = logging.getLogger("GazerAdminAPI")

# ---------------------------------------------------------------------------
# Runtime globals
# ---------------------------------------------------------------------------

# In-process asyncio.Queue for Web → Agent chat messages.
API_QUEUES: Dict[str, Any] = {"input": None}

# Eval / training managers (lazy-init, not in AppContext)
EVAL_BENCHMARK_MANAGER: Optional[Any] = None
TRAINING_JOB_MANAGER: Optional[Any] = None
TRAINING_BRIDGE_MANAGER: Optional[Any] = None
ONLINE_POLICY_LOOP_MANAGER: Optional[Any] = None
PERSONA_EVAL_MANAGER: Optional[Any] = None
PERSONA_RUNTIME_MANAGER: Optional[Any] = None


# ---------------------------------------------------------------------------
# Accessor functions — canonical reads go through AppContext
# ---------------------------------------------------------------------------

def _ctx():
    return get_app_context()

def get_usage_tracker():
    ctx = _ctx()
    return ctx.usage_tracker if ctx else None

def get_llm_router():
    ctx = _ctx()
    return ctx.llm_router if ctx else None

def get_trajectory_store():
    ctx = _ctx()
    return ctx.trajectory_store if ctx else None

def get_prompt_cache_tracker():
    ctx = _ctx()
    return ctx.prompt_cache_tracker if ctx else None

def get_tool_batching_tracker():
    ctx = _ctx()
    return ctx.tool_batching_tracker if ctx else None

def get_tool_registry():
    ctx = _ctx()
    return ctx.tool_registry if ctx else None

def get_canvas_state():
    ctx = _ctx()
    return ctx.canvas_state if ctx else None

def get_cron_scheduler():
    ctx = _ctx()
    return ctx.cron_scheduler if ctx else None

def get_hook_bus():
    ctx = _ctx()
    return ctx.hook_bus if ctx else None

def get_hook_token():
    ctx = _ctx()
    return ctx.hook_token if ctx else None

def get_personality():
    ctx = _ctx()
    return ctx.personality if ctx else None

def get_gmail_push_manager():
    ctx = _ctx()
    return ctx.gmail_push_manager if ctx else None

def get_whatsapp_channel():
    ctx = _ctx()
    return ctx.whatsapp_channel if ctx else None

def get_teams_channel():
    ctx = _ctx()
    return ctx.teams_channel if ctx else None

def get_google_chat_channel():
    ctx = _ctx()
    return ctx.google_chat_channel if ctx else None




# ---------------------------------------------------------------------------
# Lazy service accessors (avoid circular imports at module load time)
# ---------------------------------------------------------------------------

def get_provider_registry():
    from runtime.provider_registry import get_provider_registry as _impl
    return _impl()

def get_deployment_orchestrator():
    from runtime.deployment_orchestrator import get_deployment_orchestrator as _impl
    return _impl()

def get_evolution():
    from soul.evolution import get_evolution as _impl
    return _impl()

def get_owner_manager():
    from security.owner import get_owner_manager as _impl
    return _impl()


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/tools/admin -> project root
_FAVICON_ICO_PATH = _PROJECT_ROOT / "web" / "public" / "favicon.ico"

_POLICY_AUDIT_LOG_PATH = _PROJECT_ROOT / "data" / "observability" / "policy_audit.jsonl"
_STRATEGY_SNAPSHOT_LOG_PATH = _PROJECT_ROOT / "data" / "observability" / "strategy_snapshot.jsonl"
_WEB_ONBOARDING_GUIDE_PATH = _PROJECT_ROOT / "assets" / "WEB_ONBOARDING_GUIDE.md"
_MEMORY_TURN_HEALTH_LOG_PATH = resolve_runtime_path(
    "data/reports/memory_turn_health.jsonl",
    config_manager=config,
)
_TOOL_PERSIST_LOG_PATH = resolve_runtime_path(
    "data/reports/tool_result_persistence.jsonl",
    config_manager=config,
)
_EXPORT_DEFAULT_DIR = "data/reports"
_EXPORT_DEFAULT_ALLOWED_DIRS = ["data/reports", ".tmp_pytest", "exports"]
_PROTECTED_EXPORT_TARGETS = {
    (_PROJECT_ROOT / "config" / "settings.yaml").resolve(),
    (_PROJECT_ROOT / "config" / "owner.json").resolve(),
}

# Paths for atomic config update
_ATOMIC_OBJECT_UPDATE_PATHS = (
    "security.owner_channel_ids",
)

# ---------------------------------------------------------------------------
# In-memory log / audit buffers (shared across routers)
# ---------------------------------------------------------------------------
_log_buffer: collections.deque = collections.deque(maxlen=500)
_policy_audit_buffer: collections.deque = collections.deque(maxlen=300)
_strategy_change_history: collections.deque = collections.deque(maxlen=300)
_llm_history: collections.deque = collections.deque(maxlen=200)

_alert_buffer: collections.deque = collections.deque(maxlen=500)

# Coding / benchmark history
_coding_quality_history: collections.deque = collections.deque(maxlen=400)
_coding_benchmark_history: collections.deque = collections.deque(maxlen=200)
_coding_benchmark_scheduler_state: Dict[str, Any] = {"last_run_ts": 0.0, "last_result": None}
_gui_simple_benchmark_history: collections.deque = collections.deque(maxlen=200)

# MCP rate-limit state
_mcp_rate_counts: Dict[str, list] = {}
_mcp_audit_buffer: collections.deque = collections.deque(maxlen=500)

import contextvars
_mcp_request_ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "mcp_request_ctx",
    default=None,
)
