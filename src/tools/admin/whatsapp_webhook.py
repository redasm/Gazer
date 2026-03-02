"""WhatsApp Cloud API webhook routes.

Handles Meta's webhook verification (GET) and incoming message
notifications (POST).  The actual message processing is delegated
to the ``WhatsAppChannel`` adapter stored in ``_shared.WHATSAPP_CHANNEL``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query, Request, Response

from tools.admin._shared import WHATSAPP_CHANNEL

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
    if WHATSAPP_CHANNEL is None:
        response.status_code = 503
        return {"error": "WhatsApp channel not configured"}

    result = WHATSAPP_CHANNEL.verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if result is not None:
        # Meta expects the raw challenge value (plain text integer)
        return Response(content=result, media_type="text/plain")

    response.status_code = 403
    return {"error": "Verification failed"}


# ---------------------------------------------------------------------------
# POST /webhooks/whatsapp  — Incoming messages from Meta
# ---------------------------------------------------------------------------

@router.post("/webhooks/whatsapp")
async def whatsapp_incoming(request: Request, response: Response) -> Dict[str, str]:
    """Receive and process incoming WhatsApp messages."""
    if WHATSAPP_CHANNEL is None:
        response.status_code = 503
        return {"error": "WhatsApp channel not configured"}

    body = await request.body()

    # Validate signature if webhook_secret is set
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not WHATSAPP_CHANNEL.validate_signature(body, signature):
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
        await WHATSAPP_CHANNEL.handle_webhook(payload)
    except Exception as exc:
        logger.error("WhatsApp webhook processing error: %s", exc, exc_info=True)

    # Always return 200 to Meta to acknowledge receipt
    return {"status": "ok"}
