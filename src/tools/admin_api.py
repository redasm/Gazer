import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse

from runtime.app_context import AppContext, set_app_context
from runtime.config_manager import config

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


def _resolve_web_dist_dir() -> Optional[Path]:
    """Locate ``web/dist`` for the React admin UI.

    When Gazer is installed into site-packages (e.g. Docker ``pip install .``),
    ``__file__`` lives under ``site-packages/``; walking upward never reaches the
    app workdir, so we check ``GAZER_PROJECT_ROOT`` and ``cwd`` (Docker ``WORKDIR``)
    before scanning ancestors (editable / source runs).
    """
    env_root = os.environ.get("GAZER_PROJECT_ROOT", "").strip()
    if env_root:
        env_dist = Path(env_root) / "web" / "dist"
        if env_dist.is_dir():
            return env_dist
    cwd_dist = Path.cwd() / "web" / "dist"
    if cwd_dist.is_dir():
        return cwd_dist
    here = Path(__file__).resolve()
    for p in here.parents:
        cand = p / "web" / "dist"
        if cand.is_dir():
            return cand
    return None


_WEB_DIST_DIR = _resolve_web_dist_dir()

# --- Serve built React frontend from web/dist (production / Docker) ---
if _WEB_DIST_DIR.is_dir():
    from starlette.staticfiles import StaticFiles as _StaticFiles

    # Serve static assets (JS/CSS/images) at /assets
    _assets_dir = _WEB_DIST_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", _StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # Mount /icons as static files (SVG app icons referenced by manifest.json)
    _icons_dir = _WEB_DIST_DIR / "icons"
    if _icons_dir.is_dir():
        app.mount("/icons", _StaticFiles(directory=str(_icons_dir)), name="static-icons")

    # SPA fallback: serve static files from dist root when they exist, else index.html
    _index_html = _WEB_DIST_DIR / "index.html"
    if _index_html.is_file():
        @app.get("/{path:path}", include_in_schema=False)
        async def _spa_fallback(path: str):
            # Serve known root-level static files (manifest.json, favicon.ico, …)
            # before falling back to the SPA shell.
            if path:
                candidate = (_WEB_DIST_DIR / path).resolve()
                try:
                    candidate.relative_to(_WEB_DIST_DIR.resolve())
                    if candidate.is_file():
                        return FileResponse(str(candidate))
                except ValueError:
                    pass  # path traversal attempt — fall through to index.html
            return FileResponse(str(_index_html), media_type="text/html")


# --- Dynamic CORS middleware (reads config on every request) ---
from tools.admin.auth import _get_cors_config, _is_allowed_origin


@app.middleware("http")
async def _dynamic_cors(request: Request, call_next):
    origin = request.headers.get("origin", "")
    _, credentials = _get_cors_config()
    is_allowed = bool(origin and _is_allowed_origin(origin))

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


from tools.admin.error_handlers import install_log_handler
from tools.admin.websockets import ConnectionManager as _ConnectionManager

_gazer_handler = install_log_handler(_state._log_buffer, _state._llm_history)
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

    _preload_history_buffers()

    _origins, _creds = _get_cors_config()
    logger.info(
        "Admin API initialised (in-process). CORS origins=%s, credentials=%s",
        _origins,
        _creds,
    )
