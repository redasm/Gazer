"""WhatsApp Cloud API webhook routes.

Handles Meta's webhook verification (GET) and incoming message
notifications (POST).  The actual message processing is delegated
to the ``WhatsAppChannel`` adapter stored in ``_shared.WHATSAPP_CHANNEL``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query, Request, Response
from tools.admin.state import get_whatsapp_channel


logger = logging.getLogger("GazerAdminAPI")

router = APIRouter(tags=["whatsapp"])


# ---------------------------------------------------------------------------
# GET  /webhooks/whatsapp  — Meta verification challenge
# ---------------------------------------------------------------------------

@router.get("/webhooks/whatsapp")
async def whatsapp_verify(
    response: Response,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> Any:
    """Respond to Meta's webhook verification request."""
    channel = get_whatsapp_channel()
    if channel is None:
        response.status_code = 503
        return {"error": "WhatsApp channel not configured"}

    result = channel.verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if result is not None:
        return Response(content=result, media_type="text/plain")

    response.status_code = 403
    return {"error": "Verification failed"}


# ---------------------------------------------------------------------------
# POST /webhooks/whatsapp  — Incoming messages from Meta
# ---------------------------------------------------------------------------

@router.post("/webhooks/whatsapp")
async def whatsapp_incoming(request: Request, response: Response) -> Dict[str, str]:
    """Receive and process incoming WhatsApp messages."""
    channel = get_whatsapp_channel()
    if channel is None:
        response.status_code = 503
        return {"error": "WhatsApp channel not configured"}

    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not channel.validate_signature(body, signature):
        logger.warning("WhatsApp webhook signature validation failed")
        response.status_code = 403
        return {"error": "Invalid signature"}

    try:
        import json
        payload = json.loads(body)
    except Exception:
        response.status_code = 400
        return {"error": "Invalid JSON"}

    try:
        await channel.handle_webhook(payload)
    except Exception as exc:
        logger.error("WhatsApp webhook processing error: %s", exc, exc_info=True)

    return {"status": "ok"}
