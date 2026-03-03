"""Global exception handlers and structured log handler for the Admin API.

Extracted from ``admin_api.py`` to keep the application entry point focused on
FastAPI app creation, middleware, and router registration.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("GazerAdminAPI")


# ---------------------------------------------------------------------------
# Exception handlers -- register via ``install_exception_handlers(app)``
# ---------------------------------------------------------------------------

async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Handle HTTP exceptions with consistent JSON response format."""
    return Response(
        content=json.dumps({
            "error": True,
            "status_code": exc.status_code,
            "detail": exc.detail,
        }),
        status_code=exc.status_code,
        media_type="application/json",
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    """Handle request validation errors with detailed field information."""
    errors = []
    for error in exc.errors():
        errors.append({
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        })
    return Response(
        content=json.dumps({
            "error": True,
            "status_code": 422,
            "detail": "Validation error",
            "errors": errors,
        }),
        status_code=422,
        media_type="application/json",
    )


async def general_exception_handler(request: Request, exc: Exception) -> Response:
    """Catch-all handler for unexpected exceptions.

    Logs the full traceback but returns a sanitized response to prevent
    information leakage to clients.
    """
    logger.exception("Unhandled exception in API request: %s %s", request.method, request.url.path)
    return Response(
        content=json.dumps({
            "error": True,
            "status_code": 500,
            "detail": "Internal server error",
        }),
        status_code=500,
        media_type="application/json",
    )


def install_exception_handlers(app) -> None:
    """Register all exception handlers on the FastAPI *app*."""
    app.exception_handler(StarletteHTTPException)(http_exception_handler)
    app.exception_handler(RequestValidationError)(validation_exception_handler)
    app.exception_handler(Exception)(general_exception_handler)


# ---------------------------------------------------------------------------
# GazerLogHandler -- structured log handler that feeds the admin log viewer
# ---------------------------------------------------------------------------

class GazerLogHandler(logging.Handler):
    """Custom log handler that stores logs in memory for API access.

    Captures structured metadata from log records when available
    (e.g. ``request_id``, ``model``, ``tokens`` from LLM calls).
    """
    _META_KEYS = ("request_id", "model", "tokens")

    def __init__(self, log_buffer, llm_history) -> None:
        super().__init__()
        self._log_buffer = log_buffer
        self._llm_history = llm_history

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: Dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage(),
            }
            meta: Dict[str, Any] = {}
            for key in self._META_KEYS:
                val = getattr(record, key, None)
                if val is not None:
                    meta[key] = val
            if meta:
                entry["meta"] = meta
            self._log_buffer.append(entry)

            if meta.get("request_id"):
                self._llm_history.append({
                    "timestamp": entry["timestamp"],
                    "request_id": meta.get("request_id"),
                    "model": meta.get("model"),
                    "tokens": meta.get("tokens"),
                    "message": record.getMessage(),
                    "level": record.levelname,
                })
        except Exception:
            self.handleError(record)


def install_log_handler(log_buffer, llm_history, level: int = logging.DEBUG) -> GazerLogHandler:
    """Create a :class:`GazerLogHandler` and attach it to the root logger."""
    handler = GazerLogHandler(log_buffer, llm_history)
    handler.setLevel(level)
    logging.getLogger().addHandler(handler)
    return handler
