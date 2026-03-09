"""Runtime globals and in-memory buffers for the Admin API layer.

All module-level globals that ``brain.py`` injects at startup live here.
Router sub-modules should import from this module (via ``_shared``) instead
of ``admin_api.py`` to avoid circular dependencies.

Convention:
    * UPPER_CASE names are *injected at runtime* by ``brain.py``.
      They start as ``None`` and are set once during startup.
    * ``_lowercase`` buffers are shared circular buffers used across routers.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from runtime.app_context import get_app_context
from runtime.config_manager import config

# Request size guardrails (read once at import time)
_MAX_WS_MESSAGE_BYTES = int(config.get("api.max_ws_message_bytes", 256 * 1024))
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))

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


# Satellite
from devices.satellite_session import create_satellite_session_manager
SATELLITE_SOURCES: Dict[str, Any] = {}
SATELLITE_SESSION_MANAGER = create_satellite_session_manager(config)


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
_WORKFLOW_GRAPH_DIR = _PROJECT_ROOT / "workflows" / "graphs"
_POLICY_AUDIT_LOG_PATH = _PROJECT_ROOT / "data" / "observability" / "policy_audit.jsonl"
_STRATEGY_SNAPSHOT_LOG_PATH = _PROJECT_ROOT / "data" / "observability" / "strategy_snapshot.jsonl"
_WEB_ONBOARDING_GUIDE_PATH = _PROJECT_ROOT / "assets" / "WEB_ONBOARDING_GUIDE.md"
_MEMORY_TURN_HEALTH_LOG_PATH = _PROJECT_ROOT / "data" / "reports" / "memory_turn_health.jsonl"
_TOOL_PERSIST_LOG_PATH = _PROJECT_ROOT / "data" / "reports" / "tool_result_persistence.jsonl"
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
_workflow_run_history: collections.deque = collections.deque(maxlen=300)
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

# ---------------------------------------------------------------------------
# Backward-compat: module-level __getattr__ for removed globals
# ---------------------------------------------------------------------------
_COMPAT_GETTERS = {
    'CANVAS_STATE': get_canvas_state,
    'GMAIL_PUSH_MANAGER': get_gmail_push_manager,
    'CRON_SCHEDULER': get_cron_scheduler,
    '_LOCAL_CRON_SCHEDULER_ACTIVE': lambda: False,
    'TOOL_REGISTRY': get_tool_registry,
    'LLM_ROUTER': get_llm_router,
    'PROMPT_CACHE_TRACKER': get_prompt_cache_tracker,
    'TOOL_BATCHING_TRACKER': get_tool_batching_tracker,
    'TRAJECTORY_STORE': get_trajectory_store,
    'HOOK_BUS': get_hook_bus,
    'HOOK_TOKEN': get_hook_token,
    'WHATSAPP_CHANNEL': get_whatsapp_channel,
    'TEAMS_CHANNEL': get_teams_channel,
    'GOOGLE_CHAT_CHANNEL': get_google_chat_channel,
    'USAGE_TRACKER': get_usage_tracker,
}

def __getattr__(name: str):
    fn = _COMPAT_GETTERS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
