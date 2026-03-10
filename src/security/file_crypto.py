"""Secure file encryption for sensitive data storage.

Uses AES-256-GCM with a key derived from a per-user random seed combined
with machine identifiers.  The random seed is generated once on first run
and stored under the user's home directory, ensuring different OS users on
the same machine produce different keys.
"""

import base64
import hashlib
import json
import logging
import os
import platform
import secrets
import stat
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("FileCrypto")

# Conditional import for cryptography
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning(
        "cryptography library not installed. File encryption disabled. "
        "Install with: pip install cryptography"
    )

_KEY_VERSION = 2
_SEED_LENGTH = 32


def _seed_path() -> Path:
    """Return the path to the per-user random seed file."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home()
    return base / ".gazer" / "key_seed"


def _read_or_create_seed() -> bytes:
    """Read the per-user seed, creating it on first run."""
    path = _seed_path()
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) >= _SEED_LENGTH:
            return raw[:_SEED_LENGTH]
        logger.warning("Seed file too short, regenerating.")

    path.parent.mkdir(parents=True, exist_ok=True)
    seed = secrets.token_bytes(_SEED_LENGTH)
    path.write_bytes(seed)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    logger.info("Generated new encryption seed at %s", path)
    return seed


def _get_machine_components() -> list:
    """Collect non-secret machine identifiers for key mixing."""
    components = [platform.node(), platform.system(), platform.machine()]
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            proc_id, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            components.append(str(proc_id))
        except Exception:
            pass
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                capture_output=True, text=True, timeout=5,
            )
            uuid_line = result.stdout.strip()
            if uuid_line:
                components.append(uuid_line)
        except Exception:
            pass
    return components


def _get_machine_key() -> bytes:
    """Derive a 32-byte AES-256 key from per-user seed + machine identifiers.

    The per-user random seed ensures that different OS users on the same
    machine produce different encryption keys.  Machine identifiers add an
    extra binding so the encrypted file cannot be trivially moved to another
    host.
    """
    seed = _read_or_create_seed()
    machine_info = "|".join(_get_machine_components()).encode("utf-8")
    key_material = seed + hashlib.sha256(machine_info).digest()
    salt = b"Gazer-FileCrypto-v2"
    return hashlib.pbkdf2_hmac("sha256", key_material, salt, iterations=100000)


class SecureFileStorage:
    """Encrypted file storage for sensitive data like admin tokens.
    
    Features:
    - AES-256-GCM authenticated encryption
    - Machine-specific key derivation (no external key management needed)
    - File permission hardening on supported platforms
    """

    def __init__(self, file_path: str):
        """Initialize secure storage.

        Args:
            file_path: Path to the encrypted storage file
        """
        self.file_path = file_path
        self._cipher: Optional[AESGCM] = None

        if CRYPTO_AVAILABLE:
            key = _get_machine_key()
            self._cipher = AESGCM(key)
        else:
            raise RuntimeError(
                "cryptography library required for secure storage. "
                "Install with: pip install cryptography"
            )
    
    def save(self, data: Dict[str, Any]) -> None:
        """Save data to encrypted file.
        
        Args:
            data: Dictionary to encrypt and save
        """
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        
        # Encrypt with AES-GCM
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        ciphertext = self._cipher.encrypt(nonce, serialized.encode("utf-8"), None)

        # Store nonce + ciphertext
        encrypted_blob = base64.b64encode(nonce + ciphertext).decode("ascii")
        payload = {
            "version": 1,
            "encrypted": True,
            "data": encrypted_blob,
        }
        
        # Atomic write
        os.makedirs(os.path.dirname(self.file_path) or ".", exist_ok=True)
        temp_path = f"{self.file_path}.tmp"

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        # Harden file permissions (owner read/write only)
        try:
            os.chmod(temp_path, 0o600)
        except Exception:
            pass  # Windows doesn't support POSIX permissions

        # Atomic replace — no .bak left behind to leak sensitive data
        os.replace(temp_path, self.file_path)

        logger.info("Saved encrypted data to %s", self.file_path)
    
    def load(self) -> Dict[str, Any]:
        """Load and decrypt data from file.
        
        Returns:
            Decrypted dictionary
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If decryption fails or format is invalid
        """
        if not os.path.exists(self.file_path):
            return {}
        
        with open(self.file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        
        version = payload.get("version", 1)
        if version != 1:
            raise ValueError(f"Unsupported storage version: {version}")
        
        encrypted = payload.get("encrypted", False)
        
        if encrypted:
            if not self._cipher:
                raise RuntimeError(
                    "Cannot decrypt file: cryptography library not available"
                )
            
            encrypted_blob = payload["data"]
            blob_bytes = base64.b64decode(encrypted_blob)
            
            # Extract nonce and ciphertext
            nonce = blob_bytes[:12]
            ciphertext = blob_bytes[12:]
            
            try:
                plaintext = self._cipher.decrypt(nonce, ciphertext, None)
                data = json.loads(plaintext.decode("utf-8"))
                return data
            except Exception as e:
                raise ValueError(f"Decryption failed: {e}") from e
        raise ValueError("Plaintext secure storage payloads are no longer supported")
    
