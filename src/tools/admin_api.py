import asyncio
import base64
import copy
import collections
import contextvars
import csv
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import platform
import re
import shlex
import shutil
import subprocess as _subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse, PlainTextResponse
from fastapi import Request
from runtime.app_context import AppContext, get_app_context, set_app_context
from PIL import Image
import yaml

from runtime.config_manager import config, is_sensitive_config_path
from runtime.deployment_orchestrator import get_deployment_orchestrator
from runtime.provider_registry import get_provider_registry
from eval.benchmark import EvalBenchmarkManager
from eval.online_policy_loop import OnlinePolicyLoopManager
from eval.gui_simple_benchmark import GuiSimpleBenchmarkRunner, build_default_gui_simple_cases
from eval.persona_consistency import PersonaConsistencyManager
from eval.self_evolution_replay import build_default_replays, compare_planning_strategies
from eval.training_bridge import TrainingBridgeManager
from eval.trainer import TrainingJobManager
from flow.flowise_interop import flowise_to_gazer, gazer_to_flowise, flowise_migration_suggestion
from agent.agents_md import resolve_agents_overlay
from agent.agents_md_lint import lint_agents_overlay
from agent.persona_tool_policy import evaluate_persona_tool_policy_linkage
from llm.router import list_router_strategy_templates, resolve_router_strategy_template
from plugins.loader import PluginLoader
from plugins.manifest import parse_manifest
from runtime.resilience import classify_error_message
from security.owner import get_owner_manager
from security.threat_scan import scan_directory as threat_scan_directory
from soul.persona_runtime import PersonaRuntimeManager
from tools.registry import ToolPolicy, normalize_tool_policy
from soul.evolution import get_evolution
from devices.satellite_protocol import (
    FRAME_TYPE_ACK,
    FRAME_TYPE_FRAME,
    FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_HELLO,
    FRAME_TYPE_INVOKE_RESULT,
    FRAME_TYPE_ERROR,
    ensure_frame,
    ensure_hello,
    ensure_invoke_result,
    SatelliteProtocolError,
    SessionMetadata,
)
from devices.satellite_session import SatelliteSessionManager, create_satellite_session_manager

logger = logging.getLogger("GazerAdminAPI")

# ---------------------------------------------------------------------------
# Shared globals -- canonical home is tools.admin._shared
# brain.py injects into _shared; we re-export here for backward compat.
# ---------------------------------------------------------------------------
import tools.admin._shared as _shared  # noqa: E402
import tools.admin.state as _state  # noqa: E402  -- mutations go here

API_QUEUES = _shared.API_QUEUES

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tools.canvas import CanvasState
    from scheduler.cron import CronScheduler
    from bus.queue import MessageBus
    from tools.registry import ToolRegistry

# Re-export mutable globals as module-level names.
# NOTE: Because these are reassigned by brain.py via _shared, code that reads
# ``admin_api.CANVAS_STATE`` must go through ``_shared.<name>`` to see
# the injected value.  For the transition period we keep these aliases so
# existing ``from tools.admin_api import X`` statements continue to resolve
# at import time; runtime access should prefer ``_shared.<name>``.
CANVAS_STATE = _shared.CANVAS_STATE
GMAIL_PUSH_MANAGER = _shared.GMAIL_PUSH_MANAGER
CRON_SCHEDULER = _shared.CRON_SCHEDULER
_LOCAL_CRON_SCHEDULER_ACTIVE = _shared._LOCAL_CRON_SCHEDULER_ACTIVE
TOOL_REGISTRY = _shared.TOOL_REGISTRY
LLM_ROUTER = _shared.LLM_ROUTER
PROMPT_CACHE_TRACKER = _shared.PROMPT_CACHE_TRACKER
TOOL_BATCHING_TRACKER = _shared.TOOL_BATCHING_TRACKER
TRAJECTORY_STORE = _shared.TRAJECTORY_STORE
EVAL_BENCHMARK_MANAGER = _shared.EVAL_BENCHMARK_MANAGER
TRAINING_JOB_MANAGER = _shared.TRAINING_JOB_MANAGER
TRAINING_BRIDGE_MANAGER = _shared.TRAINING_BRIDGE_MANAGER
ONLINE_POLICY_LOOP_MANAGER = _shared.ONLINE_POLICY_LOOP_MANAGER
PERSONA_EVAL_MANAGER = _shared.PERSONA_EVAL_MANAGER
PERSONA_RUNTIME_MANAGER = _shared.PERSONA_RUNTIME_MANAGER

# Satellite
SATELLITE_SOURCES = _shared.SATELLITE_SOURCES
SATELLITE_SESSION_MANAGER = _shared.SATELLITE_SESSION_MANAGER

# Webhook
HOOK_BUS = _shared.HOOK_BUS
HOOK_TOKEN = _shared.HOOK_TOKEN

# Usage
USAGE_TRACKER = _shared.USAGE_TRACKER


_MISSING = object()
_ATOMIC_OBJECT_UPDATE_PATHS: Tuple[str, ...] = (
    # This map is edited as raw JSON in web UI and must support key deletion.
    "security.owner_channel_ids",
)


# --- Lifespan (replaces deprecated @app.on_event) ---

async def _coding_benchmark_scheduler_worker():
    """Background task: periodically run configured coding benchmark suites."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, _shared._maybe_run_scheduled_coding_benchmark)
        except Exception:
            logger.debug("Coding benchmark scheduler tick failed", exc_info=True)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler -- starts background workers on startup."""
    bench_task = asyncio.create_task(_coding_benchmark_scheduler_worker())
    cron_task: Optional[asyncio.Task] = None
    if _state.CRON_SCHEDULER is not None:
        cron_task = asyncio.create_task(_state.CRON_SCHEDULER.start())
    yield
    if _state.CRON_SCHEDULER is not None:
        _state.CRON_SCHEDULER.stop()
    if cron_task is not None:
        cron_task.cancel()
    bench_task.cancel()


app = FastAPI(
    title="Gazer Admin API",
    description="Internal administration API for Gazer",
    version="1.0.0",
    lifespan=lifespan,
)

def get_ctx(request: Request) -> AppContext:
    """FastAPI dependency to retrieve the AppContext."""
    return getattr(request.app.state, "ctx", None)

# ---------------------------------------------------------------------------
# Modular router registration (Phase 1: manually verified modules)
# ---------------------------------------------------------------------------
from tools.admin import ROUTERS as _ADMIN_ROUTERS
for _router, _prefix, _tags in _ADMIN_ROUTERS:
    if _router is not None:
        app.include_router(_router, prefix=_prefix, tags=_tags)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIST_DIR = _PROJECT_ROOT / "web" / "dist"
_FAVICON_ICO_PATH = _PROJECT_ROOT / "web" / "public" / "favicon.ico"

# --- Serve built React frontend from web/dist (production / Docker) ---
if _WEB_DIST_DIR.is_dir():
    from starlette.staticfiles import StaticFiles as _StaticFiles

    # Serve static assets (JS/CSS/images) at /assets
    _assets_dir = _WEB_DIST_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", _StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # SPA fallback: serve index.html for all non-API routes
    _index_html = _WEB_DIST_DIR / "index.html"
    if _index_html.is_file():
        @app.get("/{path:path}", include_in_schema=False)
        async def _spa_fallback(path: str):
            # Let API routes take priority (they're registered before this catch-all)
            return FileResponse(str(_index_html), media_type="text/html")
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


TaskExecutionStore = _shared.TaskExecutionStore
TASK_RUN_STORE = _shared.TASK_RUN_STORE
_coding_quality_history = collections.deque(maxlen=400)
_coding_benchmark_history = collections.deque(maxlen=200)
_coding_benchmark_scheduler_state: Dict[str, Any] = {"last_run_ts": 0.0, "last_result": None}
_gui_simple_benchmark_history = collections.deque(maxlen=200)
_TOOL_ERROR_PATTERN = re.compile(r"^Error\s+\[([A-Z0-9_]+)\]:\s*(.*)$", re.IGNORECASE)


def _resolve_favicon_file() -> tuple[Optional[Path], Optional[str]]:
    """Resolve favicon file path and media type."""
    if _FAVICON_ICO_PATH.is_file():
        return _FAVICON_ICO_PATH, "image/x-icon"
    return None, None


# --- CORS Configuration ---
# Canonical implementation lives in tools.admin.auth; import to avoid DRY violation.
from tools.admin.auth import _get_cors_config

_cors_origins, _cors_credentials = _get_cors_config()

# Request size guardrails (defensive defaults; configurable via api.* settings)
_MAX_WS_MESSAGE_BYTES = int(config.get("api.max_ws_message_bytes", 256 * 1024))
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))
_MAX_UPLOAD_BYTES = int(config.get("api.max_upload_bytes", 10 * 1024 * 1024))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --- Global Exception Handlers ---
from tools.admin.error_handlers import install_exception_handlers
install_exception_handlers(app)



# --- Memory Management API ---
from memory import MemoryManager
from memory.quality_eval import build_memory_quality_report
from memory.recall_regression import build_memory_recall_regression_report

_memory_manager: Optional[MemoryManager] = None


SKILLS_BUILTIN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
SKILLS_EXTENSION = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
SKILLS_BUILTIN_PATH = Path(SKILLS_BUILTIN)
SKILLS_EXTENSION_PATH = Path(SKILLS_EXTENSION)


# --- Cron API ---

# --- Logs API ---
from datetime import datetime

# Buffers are canonical in _shared; re-export for backward compat
_log_buffer = _shared._log_buffer
_policy_audit_buffer = _shared._policy_audit_buffer
_strategy_change_history = _shared._strategy_change_history
_alert_buffer = _shared._alert_buffer
_mcp_audit_buffer = _shared._mcp_audit_buffer
_mcp_rate_counts = _shared._mcp_rate_counts
_mcp_request_ctx = _shared._mcp_request_ctx

# Pre-load policy audit and strategy snapshot history from JSONL files
for _entry in _shared._read_jsonl_tail(_shared._POLICY_AUDIT_LOG_PATH, limit=500):
    if isinstance(_entry, dict):
        _policy_audit_buffer.append(_entry)
for _entry in _shared._read_jsonl_tail(_shared._STRATEGY_SNAPSHOT_LOG_PATH, limit=500):
    if isinstance(_entry, dict):
        _strategy_change_history.append(_entry)

# In-memory LLM call history (circular buffer)
_llm_history = _shared._llm_history
# In-memory workflow run history (circular buffer)
_workflow_run_history = _shared._workflow_run_history



# --- Structured Log Handler ---
from tools.admin.error_handlers import install_log_handler
_gazer_handler = install_log_handler(_log_buffer, _llm_history)


# --- Satellite API ---
from perception.sources.screen_remote import RemoteScreenSource

_latest_satellite_image = None


# --- Pairing Management API (inspired by OpenClaw's DM pairing) ---
from security.pairing import get_pairing_manager


# --- Health / Doctor API (inspired by OpenClaw's `doctor` command) ---



# --- Canvas / A2UI API ---

# Separate connection manager for canvas WebSocket clients
from tools.admin.websockets import ConnectionManager as _ConnectionManager
canvas_ws_manager = _ConnectionManager()


async def _canvas_on_change(canvas_state, extra=None):
    """Callback invoked by CanvasState on every mutation.

    Broadcasts the updated state to all connected canvas WebSocket clients.
    """
    payload = {"type": "canvas_update", **canvas_state.to_dict()}
    if extra:
        payload.update(extra)
    
    # Serialize securely without crashing on datetime etc., then send text manually
    import json
    from tools.admin_api import canvas_ws_manager
    raw_text = json.dumps(payload, default=str, ensure_ascii=False)
    
    disconnected = []
    for connection in list(canvas_ws_manager.active_connections):
        try:
            await connection.send_text(raw_text)
        except Exception as exc:
            logger.warning(f"Canvas WS broadcast failed: {exc}")
            disconnected.append(connection)
    for conn in disconnected:
        canvas_ws_manager.disconnect(conn)


# --- Webhook / Hooks API (inspired by OpenClaw's webhook surface) ---

# --- Gmail Pub/Sub Webhook ---

# --- Git API ---


# --- Debug API ---

def init_admin_api(ctx: AppContext) -> None:
    """Initialise Admin API state for in-process operation.

    Called by brain.py before starting uvicorn as an asyncio task.
    Sets up the shared asyncio.Queue so WebSocket/REST handlers can
    enqueue chat messages for the WebChannel.
    """
    import tools.admin.state as _st
    if _st.API_QUEUES["input"] is None:
        _st.API_QUEUES["input"] = asyncio.Queue()

    set_app_context(ctx)
    app.state.ctx = ctx

    logger.info(
        "Admin API initialised (in-process). CORS origins=%s, credentials=%s",
        _cors_origins,
        _cors_credentials,
    )
