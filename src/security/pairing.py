"""DM Pairing authentication -- inspired by OpenClaw's pairing flow.

When ``dm_policy`` is set to ``"pairing"``, unknown users who message the bot
receive a short alphanumeric pairing code instead of a response.  The owner
must approve the code via the Admin API before the user can interact.

Lifecycle
---------
1. Unknown user sends a message on any channel.
2. ``PairingManager.challenge(channel, sender_id)`` generates an 8-char code
   and stores a pending request.
3. The channel adapter replies with the code and withholds normal processing.
4. Owner calls ``POST /pairing/approve`` (or ``/pairing/reject``) with the code.
5. On approval the sender is added to the persistent allowlist.
"""

import json
import logging
import os
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from runtime.utils import atomic_write_json, file_lock, FileLockError

logger = logging.getLogger("PairingManager")

# How long a pairing code stays valid (5 minutes).
_CODE_TTL_SECONDS = 300

# Less-confusable alphanumeric alphabet (no 0/O, 1/I/l).
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_LENGTH = 8


def _generate_code() -> str:
    """Generate a random 8-char alphanumeric pairing code (no confusable chars)."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


@dataclass
class PairingRequest:
    channel: str
    sender_id: str
    code: str
    created_at: float = field(default_factory=time.time)


class PairingManager:
    """Manages DM pairing flow for all channels.

    Persist path stores approved sender IDs so they survive restarts.
    """

    def __init__(self, persist_path: Optional[str] = None) -> None:
        if persist_path is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            persist_path = os.path.join(base_dir, "pairing.json")
        self.persist_path = persist_path
        self.pending_path = persist_path.replace(".json", "_pending.json")
        # Lock files for cross-process synchronization
        self._lock_path = persist_path.replace(".json", ".lock")
        self._pending_lock_path = persist_path.replace(".json", "_pending.lock")
        self._pending: Dict[str, PairingRequest] = {}
        self._approved: Dict[str, set] = {}
        # mtime caching: avoid disk reads when the file hasn't changed.
        self._last_mtime: float = 0.0
        self._load()
        self._load_pending()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_approved(self, channel: str, sender_id: str) -> bool:
        """Check if *sender_id* on *channel* has been approved."""
        self._load_if_changed()
        return sender_id in self._approved.get(channel, set())

    def challenge(self, channel: str, sender_id: str) -> str:
        """Generate a pairing code for an unknown sender.

        If the same (channel, sender) already has a pending code that has
        not expired, return the same code.
        """
        # Check for existing unexpired challenge
        for code, req in self._pending.items():
            if req.channel == channel and req.sender_id == sender_id:
                if time.time() - req.created_at < _CODE_TTL_SECONDS:
                    return code

        code = _generate_code()
        self._pending[code] = PairingRequest(channel=channel, sender_id=sender_id, code=code)
        self._save_pending()
        logger.info(f"Pairing challenge issued: channel={channel} sender={sender_id}")
        return code

    def approve(self, code: str) -> Optional[PairingRequest]:
        """Approve a pending pairing code. Returns the request on success."""
        code = code.upper().strip()
        self._load_pending()
        req = self._pending.pop(code, None)
        if req is None:
            return None
        self._save_pending()
        self._approved.setdefault(req.channel, set()).add(req.sender_id)
        self._save()
        logger.info(f"Pairing approved: channel={req.channel} sender={req.sender_id}")
        return req

    def reject(self, code: str) -> Optional[PairingRequest]:
        """Reject a pending pairing code."""
        code = code.upper().strip()
        self._load_pending()
        req = self._pending.pop(code, None)
        if req:
            self._save_pending()
            logger.info(f"Pairing rejected: channel={req.channel} sender={req.sender_id}")
        return req

    def revoke(self, channel: str, sender_id: str) -> bool:
        """Revoke an approved sender."""
        approved_set = self._approved.get(channel, set())
        if sender_id in approved_set:
            approved_set.discard(sender_id)
            self._save()
            logger.info(f"Pairing revoked: channel={channel} sender={sender_id}")
            return True
        return False

    def add_approved(self, channel: str, sender_id: str) -> None:
        """Directly add a sender to the approved list (e.g. from config)."""
        self._approved.setdefault(channel, set()).add(sender_id)
        self._save()

    def list_pending(self) -> List[Dict]:
        """List all pending (unexpired) pairing requests."""
        self._load_pending()
        now = time.time()
        result = []
        expired_codes = []
        for code, req in self._pending.items():
            if now - req.created_at > _CODE_TTL_SECONDS:
                expired_codes.append(code)
                continue
            result.append({
                "code": req.code,
                "channel": req.channel,
                "sender_id": req.sender_id,
                "created_at": req.created_at,
                "expires_in": int(_CODE_TTL_SECONDS - (now - req.created_at)),
            })
        for code in expired_codes:
            del self._pending[code]
        if expired_codes:
            self._save_pending()
        return result

    def list_approved(self) -> Dict[str, List[str]]:
        """List all approved senders by channel."""
        self._load_if_changed()
        return {ch: sorted(senders) for ch, senders in self._approved.items()}

    # ------------------------------------------------------------------
    # Persistence (atomic writes)
    # ------------------------------------------------------------------

    def _load_if_changed(self) -> None:
        """Reload approved senders only when the persist file's mtime has changed."""
        try:
            current_mtime = os.path.getmtime(self.persist_path)
        except OSError:
            # File does not exist or is inaccessible; nothing to reload.
            return
        if current_mtime != self._last_mtime:
            self._load()

    def _load(self) -> None:
        """Load approved senders with file locking for cross-process safety."""
        if not os.path.exists(self.persist_path):
            self._approved = {}
            self._last_mtime = 0.0
            return
        try:
            loaded: Dict[str, set] = {}
            with file_lock(self._lock_path, timeout=5.0):
                current_mtime = os.path.getmtime(self.persist_path)
                with open(self.persist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for ch, senders in data.items():
                    if not isinstance(ch, str):
                        continue
                    if not isinstance(senders, list):
                        continue
                    loaded[ch] = {str(sender) for sender in senders if str(sender).strip()}
            self._approved = loaded
            self._last_mtime = current_mtime
            logger.info(f"Loaded pairing data: {sum(len(s) for s in self._approved.values())} approved senders")
        except FileLockError:
            logger.warning("Failed to acquire lock for loading pairing data")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load pairing data: {e}")

    def _save(self) -> None:
        """Save approved senders with file locking for cross-process safety."""
        try:
            with file_lock(self._lock_path, timeout=5.0):
                atomic_write_json(
                    self.persist_path,
                    {ch: sorted(senders) for ch, senders in self._approved.items()},
                )
        except FileLockError:
            logger.error("Failed to acquire lock for saving pairing data")
        except OSError as e:
            logger.error(f"Failed to save pairing data: {e}")

    def _load_pending(self) -> None:
        """Load pending requests with file locking (shared across Brain and Admin API processes)."""
        if not os.path.exists(self.pending_path):
            return
        try:
            with file_lock(self._pending_lock_path, timeout=5.0):
                with open(self.pending_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._pending.clear()
                for code, item in data.items():
                    self._pending[code] = PairingRequest(
                        channel=item["channel"],
                        sender_id=item["sender_id"],
                        code=code,
                        created_at=item["created_at"],
                    )
        except FileLockError:
            logger.warning("Failed to acquire lock for loading pending pairing data")
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load pending pairing data: {e}")

    def _save_pending(self) -> None:
        """Persist pending requests with file locking for cross-process safety."""
        try:
            with file_lock(self._pending_lock_path, timeout=5.0):
                data = {
                    code: {
                        "channel": req.channel,
                        "sender_id": req.sender_id,
                        "created_at": req.created_at,
                    }
                    for code, req in self._pending.items()
                }
                atomic_write_json(self.pending_path, data)
        except FileLockError:
            logger.error("Failed to acquire lock for saving pending pairing data")
        except OSError as e:
            logger.error(f"Failed to save pending pairing data: {e}")


# Lazy singleton
_pairing_manager: Optional["PairingManager"] = None


def get_pairing_manager() -> "PairingManager":
    """Return the singleton PairingManager, creating it on first access."""
    global _pairing_manager
    if _pairing_manager is None:
        _pairing_manager = PairingManager()
    return _pairing_manager


def __getattr__(name: str):
    if name == "pairing_manager":
        return get_pairing_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
