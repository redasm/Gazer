"""Owner identity -- deployer = owner.

The person who deploys Gazer **is** the owner.  No registration, no login.
The Web console is always accessible to the deployer.

For external/programmatic API access, an ``admin_token`` is auto-generated
on first run and persisted to ``config/owner.json``.  External callers
include this token as ``Authorization: Bearer <token>``.

Channel ownership
-----------------
The owner's sender IDs on each channel (Telegram, Discord, …) are read
from ``security.owner_channel_ids`` in config.  The owner always
bypasses DM pairing on those channels.
"""

import hmac
import json
import logging
import os
import secrets
import time
from typing import Dict, Optional

from runtime.config_manager import config
from runtime.utils import atomic_write_json
from security.file_crypto import SecureFileStorage

logger = logging.getLogger("OwnerManager")

_OWNER_FILE = "config/owner.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timing_safe_equal(a: str, b: str) -> bool:
    """Constant-time string comparison (prevents timing attacks)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# OwnerManager
# ---------------------------------------------------------------------------

class OwnerManager:
    """Deployer = Owner.  No registration, no login.

    A persistent ``admin_token`` is auto-generated for external API access.
    """

    def __init__(self, owner_file: str = _OWNER_FILE) -> None:
        self._file = owner_file
        self._data: Dict = {}
        # Use encrypted storage with dev-mode fallback
        env = str(os.getenv("GAZER_ENV", "dev")).strip().lower()
        allow_fallback = env in ("dev", "test", "local")
        try:
            self._storage = SecureFileStorage(
                owner_file, 
                allow_plaintext_fallback=allow_fallback
            )
            # Auto-migrate from plaintext if old file exists
            if os.path.exists(owner_file):
                try:
                    with open(owner_file, "r", encoding="utf-8") as f:
                        test_data = json.load(f)
                    # Check if it's plaintext (no "version" or "encrypted" keys)
                    if not isinstance(test_data.get("encrypted"), bool):
                        logger.info("Migrating owner.json to encrypted storage...")
                        self._storage.save(test_data)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to initialize encrypted storage: {e}. Using plaintext.")
            self._storage = None
        
        self._load()
        self._ensure_admin_token()
        self._ensure_session_store()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_admin_token(self) -> None:
        """If no admin_token exists yet, generate one and persist."""
        if not self._data.get("admin_token"):
            self._data["admin_token"] = secrets.token_urlsafe(32)
            self._data.setdefault("created_at", time.time())
            self._save()
            logger.info("Admin token auto-generated on first run.  "
                        f"See {self._file} for the token.")

    def _ensure_session_store(self) -> None:
        sessions = self._data.get("sessions")
        if not isinstance(sessions, dict):
            self._data["sessions"] = {}
            self._save()

    def _session_max_records(self) -> int:
        raw_limit = config.get("api.session_max_records", 200)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return 200
        return max(1, min(limit, 10000))

    def _enforce_session_record_limit(self, sessions: Dict[str, Dict]) -> bool:
        max_records = self._session_max_records()
        if len(sessions) <= max_records:
            return False

        def _created_at(item: tuple[str, Dict]) -> float:
            entry = item[1]
            try:
                return float(entry.get("created_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        overflow = len(sessions) - max_records
        oldest = sorted(sessions.items(), key=_created_at)[:overflow]
        changed = False
        for token, _ in oldest:
            sessions.pop(token, None)
            changed = True
        return changed

    def _cleanup_sessions(self) -> None:
        sessions = self._data.get("sessions")
        if not isinstance(sessions, dict):
            self._data["sessions"] = {}
            return
        now = time.time()
        changed = False
        for token in list(sessions.keys()):
            entry = sessions.get(token)
            if not isinstance(entry, dict):
                del sessions[token]
                changed = True
                continue
            revoked = bool(entry.get("revoked", False))
            expires_at = float(entry.get("expires_at", 0.0) or 0.0)
            if revoked or (expires_at > 0 and expires_at <= now):
                del sessions[token]
                changed = True
        if self._enforce_session_record_limit(sessions):
            changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------
    # Token validation (for external API callers)
    # ------------------------------------------------------------------

    def validate_admin_token(self, token: str) -> bool:
        if not token:
            return False
        admin_tok = self._data.get("admin_token", "")
        return bool(admin_tok and _timing_safe_equal(token, admin_tok))

    def validate_session(self, token: str, *, allow_admin_token: bool = True) -> bool:
        """Check if *token* matches the admin_token (timing-safe)."""
        if not token:
            return False
        if allow_admin_token and self.validate_admin_token(token):
            return True
        self._cleanup_sessions()
        sessions = self._data.get("sessions")
        if not isinstance(sessions, dict):
            return False
        record = sessions.get(token)
        if not isinstance(record, dict):
            return False
        if bool(record.get("revoked", False)):
            return False
        expires_at = float(record.get("expires_at", 0.0) or 0.0)
        now = time.time()
        if expires_at <= now:
            sessions.pop(token, None)
            self._save()
            return False
        return True

    def create_session(self, *, ttl_seconds: int = 3600, metadata: Optional[Dict] = None) -> str:
        self._cleanup_sessions()
        sessions = self._data.setdefault("sessions", {})
        now = time.time()
        ttl = max(60, min(int(ttl_seconds), 30 * 86400))
        token = f"sess_{secrets.token_urlsafe(32)}"
        sessions[token] = {
            "created_at": now,
            "expires_at": now + float(ttl),
            "revoked": False,
            "metadata": dict(metadata or {}),
        }
        self._enforce_session_record_limit(sessions)
        self._save()
        return token

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        sessions = self._data.get("sessions")
        if not isinstance(sessions, dict):
            return False
        record = sessions.get(token)
        if not isinstance(record, dict):
            return False
        record["revoked"] = True
        record["revoked_at"] = time.time()
        self._save()
        return True

    # ------------------------------------------------------------------
    # Owner identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Owner display name (from config)."""
        return str(config.get("personality.name", "Gazer Owner"))

    @property
    def admin_token(self) -> str:
        return self._data.get("admin_token", "")

    @property
    def channel_ids(self) -> Dict[str, str]:
        """Mapping of channel -> owner's sender_id, from config."""
        return config.get("security.owner_channel_ids", {}) or {}

    def is_owner_sender(self, channel: str, sender_id: str) -> bool:
        """Check if *sender_id* on *channel* is the owner."""
        owner_sid = self.channel_ids.get(channel, "")
        return bool(owner_sid) and owner_sid == sender_id

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._file):
            return
        try:
            if self._storage:
                self._data = self._storage.load()
            else:
                # Fallback to plaintext
                with open(self._file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            logger.info("Owner token loaded.")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"Failed to load owner data: {e}")

    def _save(self) -> None:
        try:
            if self._storage:
                self._storage.save(self._data)
            else:
                # Fallback to plaintext
                atomic_write_json(self._file, self._data)
        except OSError as e:
            logger.error(f"Failed to save owner data: {e}")


# Lazy singleton
_owner_manager: Optional["OwnerManager"] = None


def get_owner_manager() -> "OwnerManager":
    """Return the singleton OwnerManager, creating it on first access."""
    global _owner_manager
    if _owner_manager is None:
        _owner_manager = OwnerManager()
    return _owner_manager


# Backwards-compatible module-level attribute (lazy via __getattr__)
def __getattr__(name: str):
    if name == "owner_manager":
        return get_owner_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
