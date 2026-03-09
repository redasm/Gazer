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

import tools.admin.state as _state  # noqa: E402
from tools.admin.utils import _read_jsonl_tail


# --- Lifespan (replaces deprecated @app.on_event) ---

async def _coding_benchmark_scheduler_worker():
    """Background task: periodically run configured coding benchmark suites."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            from tools.admin.coding_helpers import _maybe_run_scheduled_coding_benchmark
            await loop.run_in_executor(None, _maybe_run_scheduled_coding_benchmark)
        except Exception:
            logger.debug("Coding benchmark scheduler tick failed", exc_info=True)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler -- starts background workers on startup."""
    bench_task = asyncio.create_task(_coding_benchmark_scheduler_worker())
    cron_task: Optional[asyncio.Task] = None
    cron = _state.get_cron_scheduler()
    if cron is not None:
        cron_task = asyncio.create_task(cron.start())
    yield
    cron = _state.get_cron_scheduler()
    if cron is not None:
        cron.stop()
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


def _resolve_favicon_file() -> tuple[Optional[Path], Optional[str]]:
    """Resolve favicon file path and media type."""
    if _FAVICON_ICO_PATH.is_file():
        return _FAVICON_ICO_PATH, "image/x-icon"
    return None, None


# --- Dynamic CORS middleware (reads config on every request) ---
from tools.admin.auth import _get_cors_config

# Request size guardrails (defensive defaults; configurable via api.* settings)
_MAX_WS_MESSAGE_BYTES = int(config.get("api.max_ws_message_bytes", 256 * 1024))
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))
_MAX_UPLOAD_BYTES = int(config.get("api.max_upload_bytes", 10 * 1024 * 1024))


@app.middleware("http")
async def _dynamic_cors(request: Request, call_next):
    origin = request.headers.get("origin", "")
    origins, credentials = _get_cors_config()
    is_allowed = origin and ("*" in origins or origin in origins)

    if request.method == "OPTIONS":
        headers = {"Vary": "Origin"}
        if is_allowed:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            headers["Access-Control-Max-Age"] = "600"
            if credentials:
                headers["Access-Control-Allow-Credentials"] = "true"
        return Response(status_code=200, headers=headers)

    response = await call_next(request)
    if is_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        if credentials:
            response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers.setdefault("Vary", "Origin")
    return response


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


from datetime import datetime
from tools.admin.error_handlers import install_log_handler
from perception.sources.screen_remote import RemoteScreenSource
from security.pairing import get_pairing_manager
from tools.admin.websockets import ConnectionManager as _ConnectionManager

_gazer_handler = install_log_handler(_state._log_buffer, _state._llm_history)
_latest_satellite_image = None
canvas_ws_manager = _ConnectionManager()


async def _canvas_on_change(canvas_state, extra=None):
    """Callback invoked by CanvasState on every mutation.

    Broadcasts the updated state to all connected canvas WebSocket clients.
    """
    payload = {"type": "canvas_update", **canvas_state.to_dict()}
    if extra:
        payload.update(extra)
    
    raw_text = json.dumps(payload, default=str, ensure_ascii=False)
    
    disconnected = []
    for connection in list(canvas_ws_manager.active_connections):
        try:
            await connection.send_text(raw_text)
        except Exception as exc:
            logger.warning("Canvas WS broadcast failed: %s", exc)
            disconnected.append(connection)
    for conn in disconnected:
        canvas_ws_manager.disconnect(conn)


def _preload_history_buffers() -> None:
    """Load persisted JSONL audit/strategy history into in-memory buffers."""
    for entry in _read_jsonl_tail(_state._POLICY_AUDIT_LOG_PATH, limit=500):
        if isinstance(entry, dict):
            _state._policy_audit_buffer.append(entry)
    for entry in _read_jsonl_tail(_state._STRATEGY_SNAPSHOT_LOG_PATH, limit=500):
        if isinstance(entry, dict):
            _state._strategy_change_history.append(entry)


def init_admin_api
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

    _preload_history_buffers()

    _origins, _creds = _get_cors_config()
    logger.info(
        "Admin API initialised (in-process). CORS origins=%s, credentials=%s",
        _origins,
        _creds,
    )
