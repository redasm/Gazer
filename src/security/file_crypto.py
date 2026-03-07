"""Secure file encryption for sensitive data storage.

Uses AES-256-GCM with a machine-specific key derived from hardware identifiers.
Designed for Windows platform compatibility.
"""

import base64
import hashlib
import json
import logging
import os
import platform
from typing import Any, Dict, Optional

logger = logging.getLogger("FileCrypto")

# Conditional import for cryptography
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning(
        "cryptography library not installed. File encryption disabled. "
        "Install with: pip install cryptography"
    )


def _get_machine_key() -> bytes:
    """Derive a machine-specific encryption key from hardware identifiers.
    
    Uses a combination of:
    - Windows: Computer name, processor info, system UUID
    - Fallback: hostname and basic system info
    
    Returns:
        32-byte key suitable for AES-256
    """
    components = []
    
    # System hostname
    components.append(platform.node())
    
    # Processor identifier (Windows-specific)
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            proc_id, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            components.append(str(proc_id))
        except Exception:
            pass
    
    # System UUID (if available)
    try:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["wmic", "csproduct", "get", "UUID"],
                capture_output=True,
                text=True,
                timeout=5
            )
            uuid_line = result.stdout.strip().split("\n")[-1].strip()
            if uuid_line and uuid_line != "UUID":
                components.append(uuid_line)
    except Exception:
        pass
    
    # Platform and architecture
    components.append(platform.system())
    components.append(platform.machine())
    
    # Combine and hash
    combined = "|".join(components)
    key_material = hashlib.sha256(combined.encode("utf-8")).digest()
    
    # Additional mixing with a static salt for this application
    salt = b"Gazer-FileCrypto-v1"
    final_key = hashlib.pbkdf2_hmac("sha256", key_material, salt, iterations=100000)
    
    return final_key


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
        
        # Atomic rename
        if os.path.exists(self.file_path):
            backup_path = f"{self.file_path}.bak"
            try:
                os.replace(self.file_path, backup_path)
            except Exception:
                pass
        
        os.replace(temp_path, self.file_path)
        
        logger.info(f"Saved encrypted data to {self.file_path}")
    
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
    
    def migrate_from_plaintext(self, plaintext_file: str) -> bool:
        """Migrate existing plaintext JSON file to encrypted storage.
        
        Args:
            plaintext_file: Path to existing plaintext JSON file
            
        Returns:
            True if migration successful, False if file doesn't exist
        """
        if not os.path.exists(plaintext_file):
            return False
        
        try:
            with open(plaintext_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self.save(data)
            
            # Backup old file
            backup_path = f"{plaintext_file}.plaintext.bak"
            os.replace(plaintext_file, backup_path)
            
            logger.info(f"Migrated {plaintext_file} to encrypted storage")
            return True
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False
