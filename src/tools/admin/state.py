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

from runtime.config_manager import config

if TYPE_CHECKING:
    from tools.canvas import CanvasState
    from scheduler.cron import CronScheduler

logger = logging.getLogger("GazerAdminAPI")

# ---------------------------------------------------------------------------
# Runtime globals -- injected by brain.py at startup
# ---------------------------------------------------------------------------

# IPC queues: input_q (UI/Web -> Brain), output_q (Brain -> UI/Web)
API_QUEUES: Dict[str, Any] = {"input": None, "output": None}

CANVAS_STATE: Optional["CanvasState"] = None
GMAIL_PUSH_MANAGER: Optional[Any] = None
CRON_SCHEDULER: Optional["CronScheduler"] = None
_LOCAL_CRON_SCHEDULER_ACTIVE: bool = False
TOOL_REGISTRY: Optional[Any] = None
LLM_ROUTER: Optional[Any] = None
ORCHESTRATOR: Optional[Any] = None
PROMPT_CACHE_TRACKER: Optional[Any] = None
TOOL_BATCHING_TRACKER: Optional[Any] = None
TRAJECTORY_STORE: Optional[Any] = None

# Eval / training managers (lazy-init)
EVAL_BENCHMARK_MANAGER: Optional[Any] = None
TRAINING_JOB_MANAGER: Optional[Any] = None
TRAINING_BRIDGE_MANAGER: Optional[Any] = None
ONLINE_POLICY_LOOP_MANAGER: Optional[Any] = None
PERSONA_EVAL_MANAGER: Optional[Any] = None
PERSONA_RUNTIME_MANAGER: Optional[Any] = None

# Webhook / hooks
HOOK_BUS: Optional[Any] = None
HOOK_TOKEN: Optional[str] = None

# Channel instances (injected by brain.py)
WHATSAPP_CHANNEL: Optional[Any] = None
TEAMS_CHANNEL: Optional[Any] = None
GOOGLE_CHAT_CHANNEL: Optional[Any] = None

# Usage / tracking
USAGE_TRACKER: Optional[Any] = None
IPC_USAGE_SNAPSHOT: Optional[Dict[str, Any]] = None
IPC_ROUTER_STATUS: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Accessor functions for runtime-injected globals
# ---------------------------------------------------------------------------
# Using ``from _shared import X`` captures the value at import time. If X is
# None when the importing module is loaded and brain.py injects it later,
# the local binding never updates.  These getters always return the *current*
# module-level value.
# ---------------------------------------------------------------------------

def get_usage_tracker():
    return USAGE_TRACKER

def get_llm_router():
    return LLM_ROUTER

def get_trajectory_store():
    return TRAJECTORY_STORE

def get_prompt_cache_tracker():
    return PROMPT_CACHE_TRACKER

def get_tool_batching_tracker():
    return TOOL_BATCHING_TRACKER

def get_tool_registry():
    return TOOL_REGISTRY

def get_orchestrator():
    return ORCHESTRATOR

def get_canvas_state():
    return CANVAS_STATE


# Satellite
from devices.satellite_session import create_satellite_session_manager
SATELLITE_SOURCES: Dict[str, Any] = {}
SATELLITE_SESSION_MANAGER = create_satellite_session_manager(config)


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
