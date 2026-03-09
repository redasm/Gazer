"""Webhook routes for Teams and Google Chat channels.

These endpoints receive incoming events from the respective platforms
and delegate processing to the channel adapter instances.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Request, Response
from tools.admin.state import get_google_chat_channel, get_teams_channel


logger = logging.getLogger("GazerAdminAPI")

router = APIRouter(tags=["channel-webhooks"])


# ---------------------------------------------------------------------------
# Microsoft Teams — Bot Framework webhook
# ---------------------------------------------------------------------------

@router.post("/webhooks/teams")
async def teams_incoming(request: Request, response: Response) -> Dict[str, str]:
    """Receive Bot Framework activities from Microsoft Teams."""
    ch = get_teams_channel()
    if ch is None:
        response.status_code = 503
        return {"error": "Teams channel not configured"}

    try:
        activity = await request.json()
    except Exception:
        response.status_code = 400
        return {"error": "Invalid JSON"}

    try:
        await ch.handle_activity(activity)
    except Exception as exc:
        logger.error("Teams webhook error: %s", exc, exc_info=True)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Google Chat — event webhook
# ---------------------------------------------------------------------------

@router.post("/webhooks/google_chat")
async def google_chat_incoming(request: Request, response: Response) -> Any:
    """Receive events from Google Chat."""
    ch = get_google_chat_channel()
    if ch is None:
        response.status_code = 503
        return {"error": "Google Chat channel not configured"}

    try:
        event = await request.json()
    except Exception:
        response.status_code = 400
        return {"error": "Invalid JSON"}

    try:
        reply = await ch.handle_event(event)
        if reply:
            return reply
    except Exception as exc:
        logger.error("Google Chat webhook error: %s", exc, exc_info=True)

    return {"status": "ok"}
