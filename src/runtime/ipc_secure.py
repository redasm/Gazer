"""Secure IPC (Inter-Process Communication) with HMAC authentication.

Wraps multiprocessing.Queue messages with HMAC-SHA256 signatures to prevent
message tampering and unauthorized access by local malicious processes.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("SecureIPC")


def _get_ipc_secret() -> bytes:
    """Get or generate IPC shared secret for HMAC signing.
    
    The secret is derived from the admin_token in owner.json (encrypted storage).
    Falls back to a random ephemeral secret if owner data is unavailable.
    
    Returns:
        32-byte secret key
    """
    try:
        from security.owner import get_owner_manager
        om = get_owner_manager()
        admin_token = om.admin_token
        if admin_token:
            # Derive IPC secret from admin_token
            secret = hashlib.pbkdf2_hmac(
                "sha256",
                admin_token.encode("utf-8"),
                b"Gazer-IPC-Secret-v1",
                iterations=100000,
            )
            return secret
    except Exception as e:
        logger.warning(f"Failed to derive IPC secret from owner.json: {e}")
    
    # Fallback: use environment variable or generate ephemeral secret
    env_secret = os.getenv("GAZER_IPC_SECRET", "").strip()
    if env_secret:
        return hashlib.sha256(env_secret.encode("utf-8")).digest()
    
    # Last resort: ephemeral random secret (process restart invalidates messages)
    logger.warning(
        "Using ephemeral IPC secret. IPC messages will not survive process restart. "
        "Set GAZER_IPC_SECRET env var for persistent secret."
    )
    return secrets.token_bytes(32)


# Global secret (initialized once per process)
_IPC_SECRET: Optional[bytes] = None


def _ensure_secret() -> bytes:
    """Lazy initialization of IPC secret."""
    global _IPC_SECRET
    if _IPC_SECRET is None:
        _IPC_SECRET = _get_ipc_secret()
    return _IPC_SECRET


def sign_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sign an IPC message with HMAC-SHA256.
    
    Args:
        payload: Message content (must be JSON-serializable)
        
    Returns:
        Signed envelope containing payload, timestamp, and signature
    """
    secret = _ensure_secret()
    timestamp = time.time()
    
    # Serialize payload deterministically
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    
    # Create signature material: timestamp + payload
    message = f"{timestamp:.6f}|{payload_json}".encode("utf-8")
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    
    envelope = {
        "_meta": {
            "version": 1,
            "timestamp": timestamp,
            "signature": signature,
        },
        "payload": payload,
    }
    
    return envelope


def verify_and_extract(envelope: Any, *, max_age_seconds: float = 300.0) -> Optional[Dict[str, Any]]:
    """Verify HMAC signature and extract payload.
    
    Args:
        envelope: Signed message envelope
        max_age_seconds: Maximum age of message (prevents replay attacks)
        
    Returns:
        Verified payload, or None if signature is invalid or message is too old
    """
    if not isinstance(envelope, dict):
        logger.debug("IPC message is not a dict, skipping verification")
        return None
    
    meta = envelope.get("_meta")
    if not isinstance(meta, dict):
        logger.debug("IPC message missing _meta, treating as unsigned")
        return None
    
    version = meta.get("version")
    if version != 1:
        logger.warning(f"Unsupported IPC message version: {version}")
        return None
    
    timestamp = meta.get("timestamp")
    signature = meta.get("signature")
    payload = envelope.get("payload")
    
    if not timestamp or not signature or payload is None:
        logger.warning("IPC message missing required fields")
        return None
    
    # Check message age (prevent replay attacks)
    now = time.time()
    age = now - float(timestamp)
    if age < 0 or age > max_age_seconds:
        logger.warning(f"IPC message too old or from future: age={age:.1f}s")
        return None
    
    # Recompute signature
    secret = _ensure_secret()
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    message = f"{timestamp:.6f}|{payload_json}".encode("utf-8")
    expected_sig = hmac.new(secret, message, hashlib.sha256).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(signature, expected_sig):
        logger.warning("IPC message signature verification failed")
        return None
    
    logger.debug(f"IPC message verified (age={age:.1f}s)")
    return payload


class SecureQueue:
    """Wrapper around multiprocessing.Queue with HMAC authentication.
    
    Usage:
        # Sender
        queue = SecureQueue(multiprocessing.Queue())
        queue.put({"type": "chat_message", "content": "Hello"})
        
        # Receiver
        msg = queue.get()  # Returns verified payload or None
    """
    
    def __init__(self, raw_queue, *, max_age_seconds: float = 300.0):
        """Initialize secure queue wrapper.
        
        Args:
            raw_queue: Underlying multiprocessing.Queue instance
            max_age_seconds: Maximum message age for replay protection
        """
        self._queue = raw_queue
        self._max_age = max_age_seconds
    
    def put(self, payload: Dict[str, Any], block: bool = True, timeout: Optional[float] = None):
        """Put a signed message into the queue.
        
        Args:
            payload: Message content (must be JSON-serializable dict)
            block: If True, block until space is available
            timeout: Maximum time to wait (seconds)
        """
        envelope = sign_message(payload)
        self._queue.put(envelope, block=block, timeout=timeout)
    
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Get and verify a message from the queue.
        
        Args:
            block: If True, block until a message is available
            timeout: Maximum time to wait (seconds)
            
        Returns:
            Verified payload, or None if verification failed
        """
        envelope = self._queue.get(block=block, timeout=timeout)
        return verify_and_extract(envelope, max_age_seconds=self._max_age)
    
    def get_nowait(self) -> Optional[Dict[str, Any]]:
        """Non-blocking get."""
        return self.get(block=False)
    
    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()
    
    def qsize(self) -> int:
        """Approximate queue size."""
        return self._queue.qsize()


def wrap_queue(raw_queue, max_age_seconds: float = 300.0) -> SecureQueue:
    """Convenience function to wrap an existing queue.
    
    Args:
        raw_queue: multiprocessing.Queue instance
        max_age_seconds: Message expiration time
        
    Returns:
        SecureQueue wrapper
    """
    return SecureQueue(raw_queue, max_age_seconds=max_age_seconds)
