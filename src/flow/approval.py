"""Approval gate: hard-stop + HMAC-signed resume tokens.

When a workflow hits an ``approve`` step, the engine creates a signed resume
token that encodes the flow name, step id, and serialized context.  The token
must be presented to ``FlowEngine.resume()`` to continue past the gate.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("FlowApproval")

# Secret for HMAC signing.
#
# Production should always provide ``GAZER_FLOW_SECRET``.
# If absent, we fall back to an ephemeral in-memory secret so we avoid
# a weak hardcoded default. Trade-off: tokens become invalid after restart.
_FLOW_SECRET_ENV = os.environ.get("GAZER_FLOW_SECRET", "").strip()
if _FLOW_SECRET_ENV:
    _SECRET = _FLOW_SECRET_ENV.encode("utf-8")
else:
    _SECRET = os.urandom(32)
    logger.warning(
        "GAZER_FLOW_SECRET is not set; using ephemeral flow secret. "
        "Resume tokens will be invalid after process restart."
    )

# Token expiry (seconds) — default 24 hours
TOKEN_TTL = int(os.environ.get("GAZER_FLOW_TOKEN_TTL", "86400"))


def _sig_v2(raw: bytes) -> str:
    """Return URL-safe full HMAC-SHA256 signature."""
    digest = hmac.new(_SECRET, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _sig_v1_legacy(raw: bytes) -> str:
    """Legacy truncated signature kept for transition compatibility."""
    return hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()[:16]


def create_resume_token(
    flow_name: str,
    step_id: str,
    context_snapshot: Dict[str, Any],
) -> str:
    """Create a signed, base64-encoded resume token.

    The token embeds the flow name, pending step, timestamp, and a
    JSON-serialized snapshot of the context needed to resume.
    """
    payload = {
        "flow": flow_name,
        "step": step_id,
        "ts": int(time.time()),
        "ctx": context_snapshot,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sig = _sig_v2(raw)
    token_bytes = base64.urlsafe_b64encode(raw)
    return f"{token_bytes.decode()}.{sig}"


def verify_resume_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode a resume token.

    Returns the payload dict on success, or ``None`` if invalid/expired.
    """
    try:
        parts = token.rsplit(".", 1)
        if len(parts) != 2:
            return None
        b64_data, sig = parts
        raw = base64.urlsafe_b64decode(b64_data)
        expected_v2 = _sig_v2(raw)
        if not hmac.compare_digest(sig, expected_v2):
            # Backward compatibility for older in-flight tokens.
            expected_v1 = _sig_v1_legacy(raw)
            if not hmac.compare_digest(sig, expected_v1):
                logger.warning("Resume token signature mismatch")
                return None
        payload = json.loads(raw)
        # Check expiry
        ts = payload.get("ts", 0)
        if time.time() - ts > TOKEN_TTL:
            logger.warning("Resume token expired (age=%ds)", int(time.time() - ts))
            return None
        return payload
    except Exception as exc:
        logger.warning("Failed to verify resume token: %s", exc)
        return None


def snapshot_context(ctx: "FlowContext") -> Dict[str, Any]:
    """Serialize a FlowContext into a JSON-safe dict for token embedding."""
    from flow.models import StepResult
    return {
        "args": ctx.args,
        "state": ctx.state,
        "steps": {
            k: {"output": v.output, "skipped": v.skipped, "error": v.error}
            for k, v in ctx.steps.items()
        },
    }


def restore_context(snapshot: Dict[str, Any]) -> "FlowContext":
    """Restore a FlowContext from a token snapshot."""
    from flow.models import FlowContext, StepResult
    steps = {}
    for k, v in snapshot.get("steps", {}).items():
        steps[k] = StepResult(output=v.get("output"), skipped=v.get("skipped", False), error=v.get("error"))
    return FlowContext(
        args=snapshot.get("args", {}),
        state=snapshot.get("state", {}),
        steps=steps,
    )
