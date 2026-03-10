"""Auth routes and authentication helpers for the Admin API.

Provides session management (create/clear HttpOnly cookies) and shared
authentication dependencies used by other routers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from urllib.parse import urlparse
from http.cookies import SimpleCookie

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from runtime.config_manager import config
from security.owner import get_owner_manager, OwnerManager
from tools.admin.state import logger


router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# CORS origins (resolved once at import time, used by auth checks)
# ---------------------------------------------------------------------------

_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:4173",
    "http://localhost:8080",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
    "http://127.0.0.1:8080",
]


def _get_cors_config():
    """Get CORS configuration from settings.

    Returns:
        (origins, allow_credentials)
    """
    raw = config.get("api.cors_origins", None)
    if raw is not None:
        if isinstance(raw, str):
            origins = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(raw, list):
            origins = [str(item).strip() for item in raw if str(item).strip()]
        else:
            origins = list(_DEFAULT_CORS_ORIGINS)
    else:
        origins = list(_DEFAULT_CORS_ORIGINS)

    credentials_raw = config.get("api.cors_credentials", None)
    if isinstance(credentials_raw, bool):
        credentials = credentials_raw
    elif credentials_raw is None:
        all_localhost = all(
            any(item.startswith(prefix) for prefix in ("http://localhost", "http://127.0.0.1"))
            for item in origins
        ) if origins else True
        credentials = all_localhost
    else:
        credentials = str(credentials_raw).strip().lower() in {"1", "true", "yes", "on"}
    return origins, credentials



# ---------------------------------------------------------------------------
# Token extraction helpers
# ---------------------------------------------------------------------------

def _extract_bearer_token(request: Request) -> str:
    """Extract bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


def _extract_cookie_token(request: Request) -> str:
    """Extract admin token from HttpOnly cookie."""
    cookie_token = request.cookies.get("admin_token", "")
    return cookie_token.strip() if cookie_token else ""


def _extract_request_token(request: Request) -> str:
    """Extract admin token from bearer header or cookie."""
    bearer = _extract_bearer_token(request)
    if bearer:
        return bearer
    return _extract_cookie_token(request)


def _is_loopback(request: Request) -> bool:
    """Check if the request originates from a loopback address."""
    client_host = request.client.host if request.client else ""
    return client_host in ("127.0.0.1", "::1", "localhost")


def _is_allowed_origin(origin: str) -> bool:
    """Check if Origin header is allowed by API CORS settings.

    Reads config dynamically so runtime changes take effect without restart.
    """
    origins, _ = _get_cors_config()

    if not origin:
        return not bool(config.get("api.cors_strict_mode", True))

    if "*" in origins:
        if bool(config.get("api.cors_strict_mode", True)):
            logger.warning(
                "Wildcard CORS origin detected in strict mode. "
                "This is a security risk. Rejecting request."
            )
            return False
        return True

    parsed_origin = urlparse(origin)
    origin_scheme = parsed_origin.scheme.lower().strip()
    origin_host = parsed_origin.netloc.lower().strip()
    if not origin_scheme or not origin_host:
        return False
    normalized_origin = f"{origin_scheme}://{origin_host}"

    for allowed in origins:
        parsed_allowed = urlparse(allowed)
        allowed_scheme = parsed_allowed.scheme.lower().strip()
        allowed_host = parsed_allowed.netloc.lower().strip()
        if not allowed_scheme or not allowed_host:
            continue
        normalized_allowed = f"{allowed_scheme}://{allowed_host}"
        if normalized_allowed == normalized_origin:
            return True
    return False



def _owner_validate_admin_token(owner_manager: "OwnerManager", token: str) -> bool:
    """Validate an admin token against the owner manager."""
    try:
        return bool(owner_manager.validate_admin_token(token))
    except Exception:
        return False


def _owner_validate_session_token(owner_manager: "OwnerManager", token: str) -> bool:
    """Validate a session token against the owner manager."""
    try:
        return bool(owner_manager.validate_session(token, allow_admin_token=False))
    except Exception:
        return False


def _session_cookie_kwargs(request: Request) -> Dict[str, Any]:
    """Build secure session cookie settings."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = (forwarded_proto.split(",", 1)[0].strip() or request.url.scheme or "").lower()
    secure_default = scheme == "https"
    secure_raw = config.get("api.cookie_secure", None)
    if secure_raw is None:
        secure = secure_default
    elif isinstance(secure_raw, str):
        secure = secure_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        secure = bool(secure_raw)

    raw_samesite = str(config.get("api.cookie_samesite", "strict")).strip().lower()
    samesite = raw_samesite if raw_samesite in {"lax", "strict", "none"} else "strict"
    if samesite == "none" and not secure:
        logger.warning("api.cookie_samesite='none' requires secure cookies; falling back to 'strict'")
        samesite = "strict"

    raw_max_age = config.get("api.session_max_age_seconds", 86400)
    try:
        max_age = max(60, min(int(raw_max_age), 30 * 86400))
    except (TypeError, ValueError):
        max_age = 86400

    return {
        "httponly": True,
        "secure": secure,
        "samesite": samesite,
        "max_age": max_age,
        "path": "/",
    }


# ---------------------------------------------------------------------------
# FastAPI dependency: verify admin token
# ---------------------------------------------------------------------------

async def verify_admin_token(request: Request):
    """Dependency: verify the caller for all API operations.

    Requires a valid admin_token.
    Enhanced security: enforces Origin validation for state-changing operations.
    """
    origin = request.headers.get("Origin", "")

    # Strict Origin validation for state-changing operations (POST/PUT/DELETE/PATCH)
    is_mutation = request.method in ("POST", "PUT", "DELETE", "PATCH")
    require_origin_mutations = bool(config.get("api.require_origin_for_mutations", True))

    if is_mutation and require_origin_mutations and not origin:
        raise HTTPException(
            status_code=403,
            detail="Origin header required for state-changing operations"
        )

    if origin and not _is_allowed_origin(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")

    om = get_owner_manager()
    token = _extract_request_token(request)
    if token:
        if _owner_validate_session_token(om, token):
            return
        bearer = _extract_bearer_token(request)
        if (
            bearer
            and bearer == token
            and bool(config.get("api.allow_admin_bearer_token", True))
            and _owner_validate_admin_token(om, token)
        ):
            return

    raise HTTPException(status_code=401, detail="Authentication required")


# ---------------------------------------------------------------------------
# WebSocket auth helpers
# ---------------------------------------------------------------------------

def _extract_ws_token(websocket: WebSocket) -> str:
    """Extract WS auth token from header/cookie/subprotocol."""
    auth_header = websocket.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    protocol_header = websocket.headers.get("sec-websocket-protocol", "")
    for item in protocol_header.split(","):
        proto = item.strip()
        if proto.startswith("auth.") and len(proto) > 5:
            return proto[5:]

    cookie_header = websocket.headers.get("cookie", "")
    if cookie_header:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            token_morsel = cookie.get("admin_token")
            if token_morsel and token_morsel.value:
                return token_morsel.value.strip()
        except Exception:
            logger.debug("Failed to parse WS cookies", exc_info=True)

    return ""


async def _verify_ws_auth(websocket: WebSocket) -> bool:
    """Verify WebSocket authentication.

    Returns True if authorized, False if the connection was rejected.
    """
    origin = websocket.headers.get("origin", "")
    if origin and not _is_allowed_origin(origin):
        await websocket.close(code=4003, reason="Origin not allowed")
        return False

    ws_token = _extract_ws_token(websocket)
    if ws_token:
        om = get_owner_manager()
        if _owner_validate_session_token(om, ws_token):
            setattr(websocket.state, "is_owner", True)
            return True
        allow_admin_bearer = bool(config.get("api.allow_admin_bearer_token", True))
        has_cookie_token = "admin_token=" in str(websocket.headers.get("cookie", ""))
        if allow_admin_bearer and (not has_cookie_token) and _owner_validate_admin_token(om, ws_token):
            setattr(websocket.state, "is_owner", True)
            return True
        await websocket.close(code=4003, reason="Invalid token")
        return False

    await websocket.close(code=4003, reason="Authentication required")
    return False


# ---------------------------------------------------------------------------
# Routes: /auth/*
# ---------------------------------------------------------------------------

@router.post("/auth/session")
async def create_admin_session(payload: Dict[str, Any], request: Request):
    """Create an HttpOnly admin session cookie from a valid admin token."""
    origin = request.headers.get("Origin", "")
    if origin and not _is_allowed_origin(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")

    token = str(payload.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="'token' is required")

    om = get_owner_manager()
    if not _owner_validate_admin_token(om, token):
        raise HTTPException(status_code=401, detail="Invalid admin token")

    cookie_cfg = _session_cookie_kwargs(request)
    max_age = int(cookie_cfg.get("max_age", 86400) or 86400)
    cookie_token = _extract_cookie_token(request)
    if cookie_token and _owner_validate_session_token(om, cookie_token):
        session_token = cookie_token
    elif hasattr(om, "create_session"):
        session_token = str(
            om.create_session(
                ttl_seconds=max_age,
                metadata={
                    "source": "web_session",
                    "client": str(request.client.host if request.client else ""),
                    "user_agent": str(request.headers.get("user-agent", ""))[:200],
                },
            )
        )
    else:
        raise RuntimeError("OwnerManager.create_session is unavailable")

    response = JSONResponse({"status": "ok"})
    response.set_cookie("admin_token", session_token, **cookie_cfg)
    return response


@router.delete("/auth/session")
async def clear_admin_session(request: Request):
    """Clear the admin session cookie."""
    origin = request.headers.get("Origin", "")
    if origin and not _is_allowed_origin(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")

    cookie_token = _extract_cookie_token(request)
    if cookie_token:
        om = get_owner_manager()
        if hasattr(om, "revoke_session"):
            try:
                om.revoke_session(cookie_token)
            except Exception:
                logger.debug("Failed to revoke admin session cookie token", exc_info=True)

    cookie_kwargs = _session_cookie_kwargs(request)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(
        "admin_token",
        path=str(cookie_kwargs.get("path", "/")),
        samesite=str(cookie_kwargs.get("samesite", "strict")),
        secure=bool(cookie_kwargs.get("secure", False)),
        httponly=True,
    )
    return response
