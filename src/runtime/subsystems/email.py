"""Email client initializer."""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("GazerBrain")


def init_email_client(config) -> Optional["EmailClient"]:
    """Build the optional EmailClient from config. Returns *None* when disabled."""
    if not config.get("email.enabled", False):
        return None

    from gazer_email.client import EmailClient

    client = EmailClient(
        imap_host=config.get("email.imap_host", "imap.gmail.com"),
        imap_port=config.get("email.imap_port", 993),
        smtp_host=config.get("email.smtp_host", "smtp.gmail.com"),
        smtp_port=config.get("email.smtp_port", 587),
        username=config.get("email.username", ""),
        password=config.get("email.password", "") or os.getenv("GAZER_EMAIL_PASSWORD", ""),
        max_body_length=config.get("email.max_body_length", 8000),
    )
    logger.info("Email client initialized.")
    return client
